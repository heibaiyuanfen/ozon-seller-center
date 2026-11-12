# -*- coding: utf-8 -*-
"""生成 5 条审核样本：补 OZON 高清主图、中文检索词、1688 搜索直达，并搭好货源/成本列。"""
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

SRC = r"C:\Users\wufeifan\Desktop\Super Browser\宜智家居\Seerfar-Category-Щетка+для+чистки+электроники.xlsx"
DST = os.path.join(os.path.dirname(SRC), "Seerfar-电子清洁刷_样本5条_待审.xlsx")
IMGDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_images")
os.makedirs(IMGDIR, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,ru;q=0.8"}

NEW_COLS = [
    "OZON高清主图", "中文检索关键词", "1688搜索直达", "货源链接", "货源商品标题",
    "拿货价(¥)", "起批量", "国内运费(¥)", "成本合计(¥)", "货源主图",
    "匹配依据", "置信度", "审核状态", "备注",
]
WIDTHS = [20, 36, 16, 26, 30, 12, 10, 13, 13, 20, 34, 10, 12, 48]

# 样本 5 条的人工校对检索词（俄文标题 -> 中文货源词）
KEYWORDS = {
    "1898568498": "7合1 数码清洁套装 灰色 键盘清洁刷 耳机清洁笔 喷雾瓶",
    "1772626218": "7合1 数码清洁套装 键盘清洁刷 耳机清洁笔 屏幕清洁",
    "3095634358": "耳机清洁刷 手机数码清洁刷 多功能小刷 3合1",
    "4451407601": "数码清洁套装 手机 耳机 键盘 清洁刷",
    "4773167780": "7合1 多功能清洁刷 白色 键盘 耳机 屏幕清洁套装",
}


def gen_kw(title):
    """批量阶段的规则兜底：俄文标题 -> 中文检索词。"""
    t = (title or "").lower()
    parts = []
    if re.search(r"7\s*в\s*1|7в1", t):
        parts.append("7合1")
    if re.search(r"3\s*в\s*1|3в1", t):
        parts.append("3合1")
    if re.search(r"2\s*в\s*1|2в1", t):
        parts.append("2合1")
    if "набор" in t:
        parts.append("清洁套装")
    if "щетк" in t or "щётк" in t:
        parts.append("清洁刷")
    if "наушник" in t:
        parts.append("耳机")
    if "клавиатур" in t:
        parts.append("键盘")
    if "экра" in t:
        parts.append("屏幕")
    if "телефон" in t:
        parts.append("手机")
    if "ноутбук" in t:
        parts.append("笔记本")
    if "бел" in t:
        parts.append("白色")
    if "сер" in t:
        parts.append("灰色")
    if "черн" in t:
        parts.append("黑色")
    if not parts:
        parts = ["数码清洁刷"]
    return " ".join(dict.fromkeys(parts)) + " 1688货源"


def fetch_ozon_image(url, sku):
    """wc300 -> wc1000 高清，转 PNG 并缩放到表格用缩略图；带重试与分辨率降级。"""
    path = os.path.join(IMGDIR, "hi_%s.png" % sku)
    if os.path.exists(path):
        im = PILImage.open(path).convert("RGB")
        w = 132
        h = int(im.height * w / im.width)
        im.resize((w, h)).save(path)
        return path, (w, h)

    variants = [
        url.replace("/wc300/", "/wc1000/"),
        url.replace("/wc300/", "/wc500/"),
        url,
    ]
    last = None
    for v in variants:
        for _ in range(2):
            try:
                r = requests.get(v, headers=H, timeout=30)
                r.raise_for_status()
                im = PILImage.open(io.BytesIO(r.content)).convert("RGB")
                w = 132
                h = int(im.height * w / im.width)
                im = im.resize((w, h))
                im.save(path)
                print("  image ok:", v[-46:], im.size)
                return path, (w, h)
            except Exception as e:
                last = e
    raise last if last else RuntimeError("image fetch failed")


wb = openpyxl.load_workbook(SRC)
ws = wb["Data"]
orig_max_row = ws.max_row
print("loaded:", ws.title, "rows:", orig_max_row, "cols:", ws.max_column)

# 样本文件只保留表头 + 前 5 条，便于用户快速确认格式
ws.delete_rows(7, orig_max_row - 6)

start = ws.max_column + 1
print("new columns start at:", get_column_letter(start))

header_fill = PatternFill("solid", fgColor="4472C4")
for j, name in enumerate(NEW_COLS):
    c = ws.cell(row=1, column=start + j, value=name)
    c.font = Font(bold=True, color="FFFFFF", size=10)
    c.fill = header_fill
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.column_dimensions[get_column_letter(start + j)].width = WIDTHS[j]
ws.row_dimensions[1].height = 30

note = ("1688 搜索页强制登录(跳转 login.taobao.com)，本次未能自动取到货源与价格；"
        "OZON 商品页返回 403，产品字段均取自原表。请登录后重试或点「1688搜索直达」人工核对。")

for r in range(2, 7):
    sku = str(ws.cell(row=r, column=6).value or "").replace(".0", "")
    title = str(ws.cell(row=r, column=4).value or "")
    imgurl = str(ws.cell(row=r, column=3).value or "")
    kw = KEYWORDS.get(sku) or gen_kw(title)
    print("row", r, "sku", sku, "kw", kw)

    # OZON 高清主图
    try:
        path, (w, h) = fetch_ozon_image(imgurl, sku)
        pic = XLImage(path)
        pic.width, pic.height = w, h
        ws.add_image(pic, "%s%d" % (get_column_letter(start), r))
        ws.row_dimensions[r].height = max(100, h * 0.78)
    except Exception as e:
        ws.cell(row=r, column=start, value="图片下载失败: %s" % type(e).__name__)
        ws.row_dimensions[r].height = 100

    ws.cell(row=r, column=start + 1, value=kw).alignment = Alignment(wrap_text=True, vertical="center")

    link = "https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote(kw)
    c = ws.cell(row=r, column=start + 2, value="点此搜 1688")
    c.hyperlink = link
    c.font = Font(color="0563C1", underline="single")
    c.alignment = Alignment(horizontal="center", vertical="center")

    # 货源与成本：暂列「待确认」，不编造数据
    for off in range(3, 10):
        cell = ws.cell(row=r, column=start + off, value="待确认")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.cell(row=r, column=start + 10,
            value="OZON 标题/主图特征已解析；1688 同款候选待登录后以图检索").alignment = Alignment(wrap_text=True, vertical="center")
    ws.cell(row=r, column=start + 11, value="待定")
    ws.cell(row=r, column=start + 12, value="待确认")
    ws.cell(row=r, column=start + 13, value=note).alignment = Alignment(wrap_text=True, vertical="center")

wb.save(DST)
print("SAVED:", DST, "size:", os.path.getsize(DST))
