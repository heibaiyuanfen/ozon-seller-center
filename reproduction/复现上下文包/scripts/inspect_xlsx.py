# -*- coding: utf-8 -*-
"""只读探查 seerfar 导出的 xlsx 结构：表头、行数、链接列与前几行样例。"""
import json
import os
import sys

SRC = r"C:\Users\wufeifan\Desktop\Super Browser\宜智家居\Seerfar-Category-Щетка+для+чистки+электроники.xlsx"

import openpyxl

print("exists:", os.path.exists(SRC), "size:", os.path.getsize(SRC))
wb = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
print("SHEETS:", json.dumps(wb.sheetnames, ensure_ascii=False))
for ws in wb.worksheets:
    print("=== SHEET:", ws.title, "rows:", ws.max_row, "cols:", ws.max_column)
    limit = min(8, ws.max_row)
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=limit, values_only=True), 1):
        cells = [("" if c is None else str(c))[:180] for c in row]
        print("R%d:" % i, json.dumps(cells, ensure_ascii=False))
