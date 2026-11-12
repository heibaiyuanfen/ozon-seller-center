# -*- coding: utf-8 -*-
"""登录态下校准 1688 关键词搜索，并定位「以图搜款」上传入口与商品卡片结构。"""
import io
import json
import os
from urllib.parse import quote

import requests
from PIL import Image
from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "1688_storage_state.json")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
IMGDIR = os.path.join(BASE, "sample_images")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

# 1) 准备一张不缩放的高清图，供以图搜款使用
u = "https://ir.ozone.ru/s3/multimedia-1-a/wc1000/13549608730.jpg"
r = requests.get(u, headers=H, timeout=30)
r.raise_for_status()
im = Image.open(io.BytesIO(r.content)).convert("RGB")
full = os.path.join(IMGDIR, "ozon_full_1898568498.png")
im.save(full)
print("full image:", im.size, full, flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=CHROME, headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(
        storage_state=STATE, user_agent=UA, locale="zh-CN",
        viewport={"width": 1440, "height": 900})
    page = ctx.new_page()

    for kw in ["清洁刷", "键盘清洁刷", "7合1清洁套装", "数码清洁套装"]:
        url = "https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote(kw)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5500)
            body = page.inner_text("body")
            titles = page.eval_on_selector_all(
                "a[href*='detail.1688.com/offer']",
                "els => els.slice(0,6).map(a => (a.innerText||'').trim().slice(0,50))")
            print("KW", kw, "| bodylen", len(body), "| 清洁hits", body.count("清洁"),
                  "| titles", json.dumps(titles, ensure_ascii=False), flush=True)
        except Exception as e:
            print("KW", kw, "ERR", type(e).__name__, str(e)[:120], flush=True)

    print("--- file inputs ---", flush=True)
    print(json.dumps(page.eval_on_selector_all(
        "input[type=file]",
        "els => els.map(e => ({id:e.id, cls:String(e.className).slice(0,80), accept:e.accept, outer:e.outerHTML.slice(0,220)}))"),
        ensure_ascii=False), flush=True)

    print("--- image-search UI ---", flush=True)
    print(json.dumps(page.eval_on_selector_all(
        "body *",
        """els => els.filter(e => e.children.length === 0 && /以图搜款|上传图片|图搜|粘贴图片|搜同款/.test(e.textContent||''))
             .slice(0,12).map(e => ({tag:e.tagName, cls:String(e.className).slice(0,70),
                                     txt:(e.textContent||'').trim().slice(0,60),
                                     outer:e.outerHTML.slice(0,220)}))"""),
        ensure_ascii=False), flush=True)

    print("--- card structure ---", flush=True)
    print(json.dumps(page.eval_on_selector_all(
        "a[href*='detail.1688.com/offer']",
        """els => els.slice(0,3).map(a => { let p=a; for(let i=0;i<3&&p.parentElement;i++) p=p.parentElement;
             return {href:a.href.slice(0,95), text:(p.innerText||'').slice(0,260)}; })"""),
        ensure_ascii=False), flush=True)

    with open(os.path.join(BASE, "1688_search_logged.html"), "w", encoding="utf-8") as f:
        f.write(page.content())
    print("html saved", flush=True)
    browser.close()
