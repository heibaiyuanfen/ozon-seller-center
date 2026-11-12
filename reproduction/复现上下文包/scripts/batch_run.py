# -*- coding: utf-8 -*-
"""批量：在图搜结果页内换图上传（绕开 punish），增量抓货源并生成全表交付。"""
import io
import json
import os
import re
import time
from urllib.parse import quote

import requests
from PIL import Image as PILImage
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = r"C:\Users\wufeifan\Desktop\Super Browser\宜智家居\Seerfar-Category-Щетка+для+чистки+электроники.xlsx"
DST = os.path.join(os.path.dirname(SRC), "Seerfar-电子清洁刷_全表_已补全.xlsx")
STATE = os.path.join(BASE, "1688_storage_state.json")
RESULTS = os.path.join(BASE, "results.json")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
IMGDIR = os.path.join(BASE, "sample_images")
os.makedirs(IMGDIR, exist_ok=True)
AIR_URL = ("https://air.1688.com/kapp/1688-search/pc-image-search/"
           "?tab=imageSearch&imageId=1796808885482170577"
           "&imageIdList=1796808885482170577")
LIMIT = int(os.environ.get("LIMIT", "30"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

NEW_COLS = [
    "OZON高清主图", "中文检索关键词", "1688搜索直达", "货源链接", "货源商品标题",
    "拿货价(¥)", "起批量", "国内运费(¥)", "成本合计(¥)", "货源主图",
    "匹配依据", "置信度", "审核状态", "备注",
]
WIDTHS = [20, 34, 15, 24, 34, 12, 10, 13, 13, 20, 34, 10, 12, 52]

KEYWORDS = {
    "1898568498": "7合1 数码清洁套装 灰色 键盘清洁刷 耳机清洁笔 喷雾瓶",
    "1772626218": "7合1 数码清洁套装 键盘清洁刷 耳机清洁笔 屏幕清洁",
    "3095634358": "耳机清洁刷 手机数码清洁刷 多功能小刷 3合1",
    "4451407601": "数码清洁套装 手机 耳机 键盘 清洁刷",
    "4773167780": "7合1 多功能清洁刷 白色 键盘 耳机 屏幕清洁套装",
}

PARSE_CARDS = """els => els.slice(0, 6).map(e => {
  const txt = (e.innerText || '').trim();
  const rk = e.getAttribute('data-renderkey') || '';
  const mm = rk.match(/_(\\d{9,})\\s*$/);
  const offerId = mm ? mm[1] : '';
  const img = e.querySelector('img.mainImg--KwnHnbbk') || e.querySelector('img');
  const titleEl = e.querySelector('div[class*="titleText"]');
  const priceEl = e.querySelector('div[class*="priceItem"]');
  const price = priceEl ? (priceEl.innerText||'').replace(/\\s+/g,'').replace(/¥/g,'') : '';
  const fm = txt.match(/运费\\s*([\\d.]+)\\s*元/);
  const freight = fm ? fm[1] : (txt.indexOf('包邮') >= 0 ? '0' : '');
  const qm = txt.match(/(\\d+)\\s*件起批/);
  const minQty = qm ? qm[1] : '';
  const lines = txt.split('\\n').map(s => s.trim()).filter(Boolean);
  const supplier = lines.filter(l => /公司|商行|经营部|工厂|厂|店/.test(l)).pop() || '';
  const title = titleEl ? (titleEl.innerText||'').trim() : (lines[0]||'');
  const promo = /新人价|限时价|首单价|新人专享/.test(txt);
  return {offerId, title, price, freight, minQty, supplier, promo,
          img: img ? (img.getAttribute('src') || '') : '', text: txt.slice(0, 260)};
})"""


def gen_kw(title):
    t = (title or "").lower()
    parts = []
    for pat, word in [(r"7\s*в\s*1|7в1", "7合1"), (r"3\s*в\s*1|3в1", "3合1"),
                      (r"2\s*в\s*1|2в1", "2合1")]:
        if re.search(pat, t):
            parts.append(word)
    for key, word in [("набор", "清洁套装"), ("щетк", "清洁刷"), ("щётк", "清洁刷"),
                      ("наушник", "耳机"), ("клавиатур", "键盘"), ("экра", "屏幕"),
                      ("телефон", "手机"), ("ноутбук", "笔记本"), ("бел", "白色"),
                      ("сер", "灰色"), ("черн", "黑色")]:
        if key in t:
            parts.append(word)
    if not parts:
        parts = ["数码清洁刷"]
    return " ".join(dict.fromkeys(parts)) + " 1688货源"


def load_results():
    if os.path.exists(RESULTS):
        with open(RESULTS, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_results(res):
    tmp = RESULTS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, RESULTS)


def ensure_ozon_image(url, sku):
    p = os.path.join(IMGDIR, "full_%s.png" % sku)
    if os.path.exists(p):
        return p
    for v in [url.replace("/wc300/", "/wc1000/"),
              url.replace("/wc300/", "/wc500/"), url]:
        for _ in range(2):
            try:
                r = requests.get(v, headers=H, timeout=25)
                r.raise_for_status()
                im = PILImage.open(io.BytesIO(r.content)).convert("RGB")
                im.save(p)
                return p
            except Exception:
                pass
    return None


def make_thumb(src, dst):
    im = PILImage.open(src).convert("RGB")
    w = 132
    im = im.resize((w, int(im.height * w / im.width)))
    im.save(dst)
    return im.size


def fetch_img(url, dst):
    if url.startswith("//"):
        url = "https:" + url
    r = requests.get(url, headers=H, timeout=25)
    r.raise_for_status()
    im = PILImage.open(io.BytesIO(r.content)).convert("RGB")
    w = 132
    im = im.resize((w, int(im.height * w / im.width)))
    im.save(dst)
    return im.size


def search_one(page, img_path):
    old = page.url
    page.wait_for_selector("input[type=file].image-file-reader-wrapper",
                           state="attached", timeout=25000)
    page.set_input_files("input[type=file].image-file-reader-wrapper", img_path, timeout=20000)
    page.wait_for_timeout(2000)
    page.get_by_text("搜索图片", exact=True).first.click(timeout=15000)
    try:
        page.wait_for_function("(u) => window.location.href !== u", arg=old, timeout=25000)
    except Exception:
        pass
    page.wait_for_timeout(2800)
    if page.url == old:
        raise RuntimeError("imageId 未刷新")
    page.wait_for_selector("div.searchOfferWrapper--St2OXxYo", state="attached", timeout=25000)
    return page.eval_on_selector_all("div.searchOfferWrapper--St2OXxYo", PARSE_CARDS)


def confidence(cards):
    if not cards:
        return "未匹配", "", ""
    hits = [k for k in ["七合一", "7合1", "清洁刷", "清洁套装", "键盘", "耳机", "除尘", "多功能", "清洁笔"]
            if k in cards[0]["title"]]
    level = "高" if len(hits) >= 2 else ("中" if hits else "低")
    return level, "、".join(hits), "1688以图搜款Top1，共%d个候选" % len(cards)


# ---------- 1) 读取源表 ----------
wb0 = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
ws0 = wb0["Data"]
rows = list(ws0.iter_rows(min_row=2, values_only=True))
wb0.close()
print("total products:", len(rows), "limit:", LIMIT, flush=True)

# ---------- 2) 预下载本批所需 OZON 图 ----------
batch = rows[:LIMIT]
for row in batch:
    sku = str(row[5] or "").replace(".0", "")
    ensure_ozon_image(str(row[2] or ""), sku)
print("images ready", flush=True)

# ---------- 3) 增量抓货源 ----------
res = load_results()
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=CHROME, headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(storage_state=STATE, user_agent=UA, locale="zh-CN",
                              viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto(AIR_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("div.searchOfferWrapper--St2OXxYo", state="attached", timeout=30000)

    for i, row in enumerate(batch, 1):
        sku = str(row[5] or "").replace(".0", "")
        if sku in res and res[sku].get("cards"):
            print("[%d/%d] %s cached" % (i, len(batch), sku), flush=True)
            continue
        img = os.path.join(IMGDIR, "full_%s.png" % sku)
        if not os.path.exists(img):
            res[sku] = {"cards": [], "err": "OZON图片下载失败"}
            save_results(res)
            continue
        try:
            cards = search_one(page, img)
            res[sku] = {"cards": cards}
            print("[%d/%d] %s cards=%d top=%s ¥%s" % (
                i, len(batch), sku, len(cards),
                cards[0]["title"][:26] if cards else "-",
                cards[0]["price"] if cards else "-"), flush=True)
        except Exception as e:
            print("[%d/%d] %s ERR %s %s" % (
                i, len(batch), sku, type(e).__name__, str(e)[:110]), flush=True)
            res[sku] = {"cards": [], "err": str(e)[:120]}
            try:
                page.goto(AIR_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_selector("div.searchOfferWrapper--St2OXxYo",
                                       state="attached", timeout=25000)
            except Exception:
                pass
        save_results(res)
        page.wait_for_timeout(2500)
    browser.close()

print("done scraping; results:", len(res), flush=True)

# ---------- 4) 生成全表交付 ----------
wb = openpyxl.load_workbook(SRC)
ws = wb["Data"]
start = ws.max_column + 1
fill = PatternFill("solid", fgColor="4472C4")
for j, name in enumerate(NEW_COLS):
    c = ws.cell(row=1, column=start + j, value=name)
    c.font = Font(bold=True, color="FFFFFF", size=10)
    c.fill = fill
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.column_dimensions[get_column_letter(start + j)].width = WIDTHS[j]
ws.row_dimensions[1].height = 30

for r, row in enumerate(rows, 2):
    sku = str(ws.cell(row=r, column=6).value or "").replace(".0", "")
    kw = KEYWORDS.get(sku) or gen_kw(str(ws.cell(row=r, column=4).value or ""))
    heights = [100]

    full = os.path.join(IMGDIR, "full_%s.png" % sku)
    if os.path.exists(full):
        tp = os.path.join(IMGDIR, "t_%s.png" % sku)
        try:
            sz = make_thumb(full, tp)
            pic = XLImage(tp)
            pic.width, pic.height = sz
            ws.add_image(pic, "%s%d" % (get_column_letter(start), r))
            heights.append(sz[1])
        except Exception:
            ws.cell(row=r, column=start, value="图片待补")

    ws.cell(row=r, column=start + 1, value=kw).alignment = Alignment(wrap_text=True, vertical="center")
    lk = ws.cell(row=r, column=start + 2, value="点此搜 1688")
    lk.hyperlink = "https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote(kw)
    lk.font = Font(color="0563C1", underline="single")
    lk.alignment = Alignment(horizontal="center", vertical="center")

    info = res.get(sku, {})
    cards = info.get("cards", [])
    if cards:
        top = cards[0]
        level, hits, basis = confidence(cards)
        cl = ws.cell(row=r, column=start + 3, value=top["offerId"] or "无ID")
        if top["offerId"]:
            cl.hyperlink = "https://detail.1688.com/offer/%s.html" % top["offerId"]
            cl.font = Font(color="0563C1", underline="single")
        ws.cell(row=r, column=start + 4, value=top["title"]).alignment = Alignment(wrap_text=True, vertical="center")
        ws.cell(row=r, column=start + 5, value=top["price"] or "待补")
        ws.cell(row=r, column=start + 6, value=top["minQty"] or "待补")
        freight = top["freight"] if top["freight"] != "" else "待补"
        ws.cell(row=r, column=start + 7, value=freight)
        try:
            cost = round(float(top["price"]) + float(freight), 2)
        except (ValueError, TypeError):
            cost = "待补"
        cc = ws.cell(row=r, column=start + 8, value=cost)
        cc.font = Font(bold=True, color="C00000")

        if top["img"]:
            try:
                sp = os.path.join(IMGDIR, "s_%s.png" % sku)
                sz2 = fetch_img(top["img"], sp)
                pic2 = XLImage(sp)
                pic2.width, pic2.height = sz2
                ws.add_image(pic2, "%s%d" % (get_column_letter(start + 9), r))
                heights.append(sz2[1])
            except Exception:
                ws.cell(row=r, column=start + 9, value="货源图待补")
        else:
            ws.cell(row=r, column=start + 9, value="无图")

        ws.cell(row=r, column=start + 10,
                value="%s；标题命中：%s" % (basis, hits or "无")).alignment = Alignment(wrap_text=True, vertical="center")
        cf = ws.cell(row=r, column=start + 11, value=level)
        if level in ("低", "中", "未匹配"):
            cf.font = Font(bold=True, color="C00000")
            cf.fill = PatternFill("solid", fgColor="FFF2CC")
        notes = ["提示：1688标价常为单件/引流规格价，请核对起批规格与实际拿货价"]
        if top["promo"]:
            notes.append("页面价为新人价/限时价，请终审核对常规批发价")
        if top["supplier"]:
            notes.append("供应商：" + top["supplier"])
        for k, c in enumerate(cards[1:4], 2):
            notes.append("备选%d：¥%s / %s" % (k, c["price"], c["offerId"]))
        ws.cell(row=r, column=start + 13, value="；".join(notes)).alignment = Alignment(wrap_text=True, vertical="center")
    else:
        for off in range(3, 10):
            ws.cell(row=r, column=start + off, value="待补")
        ws.cell(row=r, column=start + 10, value="本批未跑或未取到货源")
        ws.cell(row=r, column=start + 11, value="未匹配")
        ws.cell(row=r, column=start + 13,
                value=("抓取异常：" + info.get("err", "")) if info.get("err") else "后续批次补跑")

    ws.cell(row=r, column=start + 12, value="待确认")
    ws.row_dimensions[r].height = max(heights) * 0.78

wb.save(DST)
print("SAVED:", DST, "size:", os.path.getsize(DST),
      "rows:", ws.max_row, "images:", len(ws._images), flush=True)
