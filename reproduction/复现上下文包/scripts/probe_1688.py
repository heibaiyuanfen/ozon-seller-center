# -*- coding: utf-8 -*-
"""探测 1688 关键词搜索结果的可解析性，并转换 OZON 样本图格式。"""
import os
import re
import json
from urllib.parse import quote
import requests
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "sample_images")
os.makedirs(OUT, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 1) 把 webp 内容转成真正的 png，便于后续查看
for i in (1, 2, 3):
    src = os.path.join(OUT, "ozon_%d.jpg" % i)
    if os.path.exists(src):
        im = Image.open(src).convert("RGB")
        dst = os.path.join(OUT, "ozon_%d.png" % i)
        im.save(dst)
        print("converted", dst, im.size)

# 2) 关键词搜索页
kws = ["键盘清洁刷 7合1", "多功能清洁刷 耳机 键盘", "清洁套装 7合1 电子产品"]
for kw in kws:
    url = "https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote(kw)
    try:
        r = requests.get(url, headers=H, timeout=25)
        t = r.text
        print("\n=== KW:", kw, "status:", r.status_code, "len:", len(t), "final:", r.url[:120])
        for marker in ["offerList", "g_page_config", '"price"', '"subject"',
                       "itemId", "offerId", "//cbu01.alicdn.com", "detail.1688.com",
                       "登录", "验证码", "punish", "s.1688.com/selloffer"]:
            print("  marker %-24s %d" % (marker, t.count(marker)))
        fn = os.path.join(BASE, "probe_1688_%d.html" % kws.index(kw))
        with open(fn, "w", encoding="utf-8") as f:
            f.write(t)
        # 猜测商品链接
        ids = re.findall(r'detail\.1688\.com/offer/(\d+)\.html', t)
        print("  offer ids sample:", list(dict.fromkeys(ids))[:8])
    except Exception as e:
        print("KW", kw, "ERR", type(e).__name__, str(e)[:150])
