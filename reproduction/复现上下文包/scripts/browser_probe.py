# -*- coding: utf-8 -*-
"""用系统 Chrome 渲染 1688 搜索页，验证能否读到动态商品列表。"""
import os
import json
from urllib.parse import quote
from playwright.sync_api import sync_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = os.path.dirname(os.path.abspath(__file__))
KW = "7合1 清洁套装 键盘清洁刷"
URL = "https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote(KW)

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=CHROME,
        headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
    )
    page = ctx.new_page()
    try:
        resp = page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        print("goto status:", resp.status if resp else None)
        page.wait_for_timeout(8000)
        print("title:", page.title())
        print("final url:", page.url)
        body = page.inner_text("body")
        print("body len:", len(body))
        print("--- body head ---")
        print(body[:1600])
        links = page.eval_on_selector_all(
            "a[href*='detail.1688.com/offer']", "els => els.map(e => e.href)")
        print("offer links:", json.dumps(list(dict.fromkeys(links))[:12], ensure_ascii=False))
        imgs = page.eval_on_selector_all(
            "img", "els => els.map(e => e.src).filter(s => s && s.indexOf('alicdn') > -1)")
        print("alicdn imgs:", json.dumps(imgs[:12], ensure_ascii=False))
        page.screenshot(path=os.path.join(BASE, "1688_browser.png"))
        print("screenshot saved")
    except Exception as e:
        print("ERR", type(e).__name__, str(e)[:400])
    finally:
        browser.close()
