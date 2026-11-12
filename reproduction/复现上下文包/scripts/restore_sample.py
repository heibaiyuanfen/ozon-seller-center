# -*- coding: utf-8 -*-
"""重建 5 条样本：3 条已抓到的真实货源 + 2 条因风控未完成，附缓存图片。"""
import os
from urllib.parse import quote

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from PIL import Image as PILImage

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = r"C:\Users\wufeifan\Desktop\Super Browser\宜智家居\Seerfar-Category-Щетка+для+чистки+электроники.xlsx"
DST = os.path.join(os.path.dirname(SRC), "Seerfar-电子清洁刷_样本5条_完整版.xlsx")
IMGDIR = os.path.join(BASE, "sample_images")

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

# 2026-11-12 实际从 1688 以图搜款抓到的结果
DATA = {
    "1898568498": {
        "offer": "1074893423752",
        "title": "键盘耳机清洁工具套装电脑键盘新款除尘刷七合一多功能清洁刷套装",
        "price": 0.17, "qty": 1, "freight": 2.2, "cost": 2.37,
        "conf": "高", "basis": "1688以图搜款Top1，共6个候选；标题命中：七合一、清洁刷、键盘、耳机、除尘、多功能",
        "note": "供应商：平阳县日灏电子商务商行；备选2：¥2.62 / 984003393220；备选3：¥2.9 / 977477195652；备选4：¥3.5 / 1027629494869",
    },
    "1772626218": {
        "offer": "977477195652",
        "title": "键盘清洁机械键盘清理工具电脑除尘软毛刷灰尘刷子多功能清灰手机",
        "price": 2.9, "qty": 1, "freight": 2.2, "cost": 5.1,
        "conf": "高", "basis": "1688以图搜款Top1，共6个候选；标题命中：键盘、除尘、多功能",
        "note": "供应商：义乌市婺辉贸易有限公司；备选2：¥2.62 / 984003393220；备选3：¥0.17 / 1074893423752；备选4：¥3.5 / 1027629494869",
    },
    "3095634358": {
        "offer": "777527199199",
        "title": "跨境新款Multi Cleaning Pen多用途清洁笔蓝牙耳机清洁",
        "price": 1.15, "qty": 1, "freight": 4.8, "cost": 5.95,
        "conf": "中", "basis": "1688以图搜款Top1，共6个候选；标题命中：耳机（俄文标题为耳机/手机清洁刷）",
        "note": "供应商：再宏户外体育用品(平阳县)有限公司；备选2：¥0.9 / 1044131088922；备选3：¥1.1 / 926768520916；备选4：¥0.72 / 1046360603250",
    },
}

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
    heights = [100]

    tp = os.path.join(IMGDIR, "thumb_ozon_%s.png" % sku)
    if os.path.exists(tp):
        sz = PILImage.open(tp).size
        pic = XLImage(tp)
        pic.width, pic.height = sz
        ws.add_image(pic, "%s%d" % (get_column_letter(start), r))
        heights.append(sz[1])

    ws.cell(row=r, column=start + 1, value=kw).alignment = Alignment(wrap_text=True, vertical="center")
    lk = ws.cell(row=r, column=start + 2, value="点此搜 1688")
    lk.hyperlink = "https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote(kw)
    lk.font = Font(color="0563C1", underline="single")
    lk.alignment = Alignment(horizontal="center", vertical="center")

    d = DATA.get(sku)
    if d:
        cl = ws.cell(row=r, column=start + 3, value=d["offer"])
        cl.hyperlink = "https://detail.1688.com/offer/%s.html" % d["offer"]
        cl.font = Font(color="0563C1", underline="single")
        ws.cell(row=r, column=start + 4, value=d["title"]).alignment = Alignment(wrap_text=True, vertical="center")
        ws.cell(row=r, column=start + 5, value=d["price"])
        ws.cell(row=r, column=start + 6, value=d["qty"])
        ws.cell(row=r, column=start + 7, value=d["freight"])
        cc = ws.cell(row=r, column=start + 8, value=d["cost"])
        cc.font = Font(bold=True, color="C00000")
        sp = os.path.join(IMGDIR, "thumb_src_%s.png" % sku)
        if os.path.exists(sp):
            sz2 = PILImage.open(sp).size
            pic2 = XLImage(sp)
            pic2.width, pic2.height = sz2
            ws.add_image(pic2, "%s%d" % (get_column_letter(start + 9), r))
            heights.append(sz2[1])
        else:
            ws.cell(row=r, column=start + 9, value="货源图待补")
        ws.cell(row=r, column=start + 10, value=d["basis"]).alignment = Alignment(wrap_text=True, vertical="center")
        ws.cell(row=r, column=start + 11, value=d["conf"])
        ws.cell(row=r, column=start + 13, value=d["note"]).alignment = Alignment(wrap_text=True, vertical="center")
    else:
        for off in range(3, 10):
            ws.cell(row=r, column=start + off, value="待补")
        ws.cell(row=r, column=start + 10, value="1688以图搜款未完成：触发滑块人机验证(punish)")
        ws.cell(row=r, column=start + 11, value="未匹配")
        ws.cell(row=r, column=start + 13,
                value="1688 连续图搜触发风控滑块验证，自动拖动未通过；需人工过验证后补跑。OZON 图与检索词已就绪。")

    ws.cell(row=r, column=start + 12, value="待确认")
    ws.row_dimensions[r].height = max(heights) * 0.78

wb.save(DST)
print("RESTORED:", DST, "size:", os.path.getsize(DST), "images:", len(ws._images))
