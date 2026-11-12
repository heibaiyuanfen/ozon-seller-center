# -*- coding: utf-8 -*-
"""5 条完整样本：1688 以图搜款 -> 货源/成本/两类图片回填 Excel。"""
import io
import os
import re
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
DST = os.path.join(os.path.dirname(SRC), "Seerfar-电子清洁刷_样本5条_完整版.xlsx")
STATE = os.path.join(BASE, "1688_storage_state.json")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
IMGDIR = os.path.join(BASE, "sample_images")
os.makedirs(IMGDIR, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
SEARCH_URL = "https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote("清洁刷")

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


def save_image(content_or_url, path, thumb=True):
    """下载/写入图片并转 PNG，缩略图统一 132px 宽。"""
    if isinstance(content_or_url, bytes):
        im = PILImage.open(io.BytesIO(content_or_url)).convert("RGB")
    else:
        r = requests.get(content_or_url, headers=H, timeout=25)
        r.raise_for_status()
        im = PILImage.open(io.BytesIO(r.content)).convert("RGB")
    if thumb:
        w = 132
        im = im.resize((w, int(im.height * w / im.width)))
    im.save(path)
    return im.size


def ozon_hi(url, sku):
    """返回 (用于上传的原图路径, 用于嵌入的缩略图路径, 缩略尺寸)。"""
    full_p = os.path.join(IMGDIR, "full_%s.png" % sku)
    thumb_p = os.path.join(IMGDIR, "thumb_ozon_%s.png" % sku)
    if not os.path.exists(full_p):
        variants = [url.replace("/wc300/", "/wc1000/"), url.replace("/wc300/", "/wc500/"), url]
        data = None
        for v in variants:
            try:
                rr = requests.get(v, headers=H, timeout=30)
                rr.raise_for_status()
                data = rr.content
                break
            except Exception:
                continue
        if data is None:
            raise RuntimeError("ozon image download failed")
        save_image(data, full_p, thumb=False)
    size = save_image(open(full_p, "rb").read(), thumb_p, thumb=True)
    return full_p, thumb_p, size


def image_search(page, img_path):
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1800)
    try:
        page.wait_for_selector("input[type=file].image-file-reader-wrapper",
                               state="attached", timeout=30000)
    except Exception:
        print("  debug url:", page.url[:140], "body:",
              page.inner_text("body")[:240].replace("\n", " | "), flush=True)
        raise
    page.set_input_files("input[type=file].image-file-reader-wrapper", img_path, timeout=20000)
    page.wait_for_timeout(2500)
    page.get_by_text("搜索图片", exact=True).first.click(timeout=15000)
    page.wait_for_url("**/pc-image-search/**", timeout=30000)
    page.wait_for_selector("div.searchOfferWrapper--St2OXxYo", state="attached", timeout=30000)
    page.wait_for_timeout(1200)
    return page.eval_on_selector_all("div.searchOfferWrapper--St2OXxYo", PARSE_CARDS)


def confidence(cards):
    if not cards:
        return "未匹配", "", ""
    hits = [k for k in ["七合一", "7合1", "清洁刷", "清洁套装", "键盘", "耳机", "除尘", "多功能"]
            if k in cards[0]["title"]]
    if len(hits) >= 2:
        level = "高"
    elif len(hits) == 1:
        level = "中"
    else:
        level = "低"
    return level, "、".join(hits), "1688以图搜款Top1，共%d个候选" % len(cards)


# ---------- 抓取 5 条货源 ----------
results = {}
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=CHROME, headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(storage_state=STATE, user_agent=UA, locale="zh-CN",
                              viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    wb0 = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
    ws0 = wb0["Data"]
    for idx, row in enumerate(ws0.iter_rows(min_row=2, max_row=6, values_only=True)):
        sku = str(row[5] or "").replace(".0", "")
        title_ru = str(row[3] or "")
        imgurl = str(row[2] or "")
        if idx:
            page.wait_for_timeout(4500)  # 图搜频次缓冲，避免被限流
        for attempt in (1, 2):
            try:
                full_p, thumb_p, _ = ozon_hi(imgurl, sku)
                cards = image_search(page, full_p)
                print("SKU", sku, "attempt", attempt, "cards", len(cards),
                      "top:", cards[0]["title"][:34] if cards else "-",
                      "¥", cards[0]["price"] if cards else "-", flush=True)
                results[sku] = {"cards": cards, "full": full_p, "thumb": thumb_p}
                break
            except Exception as e:
                print("SKU", sku, "attempt", attempt, "ERR",
                      type(e).__name__, str(e)[:130], flush=True)
                if attempt == 1:
                    page.wait_for_timeout(9000)
                else:
                    results[sku] = {"cards": [], "full": None, "thumb": None,
                                    "err": str(e)[:120]}
    wb0.close()
    browser.close()

# ---------- 回填 Excel ----------
wb = openpyxl.load_workbook(SRC)
ws = wb["Data"]
ws.delete_rows(7, ws.max_row - 6)
start = ws.max_column + 1

fill = PatternFill("solid", fgColor="4472C4")
for j, name in enumerate(NEW_COLS):
    c = ws.cell(row=1, column=start + j, value=name)
    c.font = Font(bold=True, color="FFFFFF", size=10)
    c.fill = fill
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.column_dimensions[get_column_letter(start + j)].width = WIDTHS[j]
ws.row_dimensions[1].height = 30

for r in range(2, 7):
    sku = str(ws.cell(row=r, column=6).value or "").replace(".0", "")
    kw = KEYWORDS.get(sku, "数码清洁刷 1688货源")
    info = results.get(sku, {})
    cards = info.get("cards", [])

    # OZON 图
    heights = [100]
    if info.get("thumb") and os.path.exists(info["thumb"]):
        size = PILImage.open(info["thumb"]).size
        pic = XLImage(info["thumb"])
        pic.width, pic.height = size
        ws.add_image(pic, "%s%d" % (get_column_letter(start), r))
        heights.append(size[1])

    ws.cell(row=r, column=start + 1, value=kw).alignment = Alignment(wrap_text=True, vertical="center")
    lk = ws.cell(row=r, column=start + 2, value="点此搜 1688")
    lk.hyperlink = "https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote(kw)
    lk.font = Font(color="0563C1", underline="single")
    lk.alignment = Alignment(horizontal="center", vertical="center")

    if cards:
        top = cards[0]
        level, hits, basis = confidence(cards)
        offer_url = "https://detail.1688.com/offer/%s.html" % top["offerId"] if top["offerId"] else ""
        cl = ws.cell(row=r, column=start + 3, value=top["offerId"] or "无ID")
        if offer_url:
            cl.hyperlink = offer_url
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
        ws.cell(row=r, column=start + 8, value=cost)
        cell = ws.cell(row=r, column=start + 8)
        cell.font = Font(bold=True, color="C00000")

        # 货源主图
        if top["img"]:
            try:
                src = top["img"]
                if src.startswith("//"):
                    src = "https:" + src
                pth = os.path.join(IMGDIR, "thumb_src_%s.png" % sku)
                sz = save_image(src, pth, thumb=True)
                pic2 = XLImage(pth)
                pic2.width, pic2.height = sz
                ws.add_image(pic2, "%s%d" % (get_column_letter(start + 9), r))
                heights.append(sz[1])
            except Exception as e:
                ws.cell(row=r, column=start + 9, value="货源图下载失败")
        else:
            ws.cell(row=r, column=start + 9, value="无图")

        ws.cell(row=r, column=start + 10,
                value="%s；标题命中：%s" % (basis, hits or "无")).alignment = Alignment(wrap_text=True, vertical="center")
        ws.cell(row=r, column=start + 11, value=level)
        notes = []
        if top["promo"]:
            notes.append("页面价为新人价/限时价，请终审核对常规批发价")
        if top["supplier"]:
            notes.append("供应商：" + top["supplier"])
        for i, c in enumerate(cards[1:4], 2):
            notes.append("备选%d：¥%s / %s / %s" % (i, c["price"], c["offerId"], c["title"][:22]))
        ws.cell(row=r, column=start + 13, value="；".join(notes)).alignment = Alignment(wrap_text=True, vertical="center")
    else:
        for off in range(3, 10):
            ws.cell(row=r, column=start + off, value="待补")
        ws.cell(row=r, column=start + 10, value="1688以图搜款未返回结果或抓取失败")
        ws.cell(row=r, column=start + 11, value="未匹配")
        ws.cell(row=r, column=start + 13, value="错误：" + info.get("err", "无候选"))

    ws.cell(row=r, column=start + 12, value="待确认")
    ws.row_dimensions[r].height = max(heights) * 0.78

wb.save(DST)
print("SAVED:", DST, "size:", os.path.getsize(DST), flush=True)
