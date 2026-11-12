# -*- coding: utf-8 -*-
"""探测 1688 以图搜款结果页的商品卡片 DOM 结构。"""
import json
import os
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "1688_storage_state.json")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
FULL_IMG = os.path.join(BASE, "sample_images", "ozon_full_1898568498.png")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=CHROME, headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(storage_state=STATE, user_agent=UA, locale="zh-CN",
                              viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote("清洁刷"),
              wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    page.set_input_files("input[type=file].image-file-reader-wrapper", FULL_IMG, timeout=20000)
    page.wait_for_timeout(3500)
    page.get_by_text("搜索图片", exact=True).first.click(timeout=12000)
    page.wait_for_timeout(12000)
    print("RESULT URL:", page.url[:200], flush=True)

    print("--- all anchors (first 25 hrefs) ---", flush=True)
    print(json.dumps(page.eval_on_selector_all(
        "a", "els => els.map(e => e.getAttribute('href')).filter(Boolean).slice(0,25)"),
        ensure_ascii=False), flush=True)

    print("--- anchors containing offer/1688 ids ---", flush=True)
    print(json.dumps(page.eval_on_selector_all(
        "a", "els => els.filter(e => /offer|item|detail/i.test((e.getAttribute('href')||''))).slice(0,12).map(e => ({href:(e.getAttribute('href')||'').slice(0,140), txt:(e.innerText||'').trim().slice(0,90)}))"),
        ensure_ascii=False), flush=True)

    print("--- candidate card containers ---", flush=True)
    print(json.dumps(page.eval_on_selector_all(
        "body *",
        """els => els.filter(e => {
              const t = e.innerText || '';
              return t.includes('¥') && (t.includes('起批') || t.includes('件'))
                     && t.length > 25 && t.length < 420;
            }).slice(0,6).map(e => ({
              tag:e.tagName, cls:String(e.className).slice(0,110), len:e.innerText.length,
              text:e.innerText.slice(0,220),
              anchors:[...e.querySelectorAll('a')].map(a=>(a.getAttribute('href')||'').slice(0,120)).slice(0,4),
              imgs:[...e.querySelectorAll('img')].map(i=>(i.getAttribute('src')||i.getAttribute('data-src')||'').slice(0,140)).slice(0,3),
              html:e.outerHTML.slice(0,700)
            }))"""),
        ensure_ascii=False), flush=True)

    print("--- elements with data offer ids ---", flush=True)
    print(json.dumps(page.eval_on_selector_all(
        "[data-offer-id], [data-item-id], [offerid], [data-id]",
        "els => els.slice(0,10).map(e => ({tag:e.tagName, cls:String(e.className).slice(0,80), attrs:[...e.attributes].map(a=>a.name+'='+a.value.slice(0,60)).slice(0,8)}))"),
        ensure_ascii=False), flush=True)

    with open(os.path.join(BASE, "1688_image_result.html"), "w", encoding="utf-8") as f:
        f.write(page.content())
    print("html saved", flush=True)
    browser.close()
