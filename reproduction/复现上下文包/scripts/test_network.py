# -*- coding: utf-8 -*-
"""样本前的网络可行性探测：OZON 图片、OZON 商品页、1688 搜索页。"""
import os
import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_images")
os.makedirs(OUT, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,ru;q=0.8,en;q=0.7",
}

imgs = [
    "https://ir.ozone.ru/s3/multimedia-1-a/wc300/13549608730.jpg",
    "https://ir.ozone.ru/s3/multimedia-1-u/wc300/14262624894.jpg",
    "https://ir.ozone.ru/s3/multimedia-1-j/wc300/7253799319.jpg",
]

print("--- OZON images (wc300) ---")
for i, u in enumerate(imgs, 1):
    try:
        r = requests.get(u, headers=H, timeout=20)
        print(i, r.status_code, r.headers.get("content-type"), len(r.content))
        if r.ok and "image" in (r.headers.get("content-type") or ""):
            with open(os.path.join(OUT, "ozon_%d.jpg" % i), "wb") as f:
                f.write(r.content)
    except Exception as e:
        print(i, "ERR", type(e).__name__, str(e)[:120])

print("--- OZON higher-res variants (first image) ---")
base = imgs[0]
for variant in [
    base.replace("/wc300/", "/wc1000/"),
    base.replace("/wc300/", "/wc500/"),
    base.replace("/wc300/", "/"),
]:
    try:
        r = requests.get(variant, headers=H, timeout=20)
        print(variant[-70:], r.status_code, r.headers.get("content-type"), len(r.content))
    except Exception as e:
        print("ERR", type(e).__name__, str(e)[:120])

print("--- OZON product page ---")
for u in ["https://www.ozon.ru/product/1898568498"]:
    try:
        r = requests.get(u, headers=H, timeout=25)
        print(u, r.status_code, r.headers.get("content-type"), len(r.content),
              "title-hint:", ("ozon" in r.text[:3000].lower()))
        print("snippet:", r.text[:300].replace("\n", " "))
    except Exception as e:
        print(u, "ERR", type(e).__name__, str(e)[:150])

print("--- 1688 reachability ---")
for u in [
    "https://www.1688.com/",
    "https://s.1688.com/selloffer/offer_search.htm?keywords=%E6%B8%85%E6%B4%81%E5%88%B7",
]:
    try:
        r = requests.get(u, headers=H, timeout=25)
        print(u[:80], r.status_code, r.headers.get("content-type"), len(r.content),
              "url_final:", r.url[:110])
        print("snippet:", r.text[:200].replace("\n", " "))
    except Exception as e:
        print(u[:80], "ERR", type(e).__name__, str(e)[:150])
