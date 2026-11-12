# -*- coding: utf-8 -*-
"""探测 1688 滑块验证页结构，并尝试自动拖动通过。"""
import json
import os
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "1688_storage_state.json")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
SEARCH_URL = "https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote("清洁刷")

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=CHROME, headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(storage_state=STATE, user_agent=UA, locale="zh-CN",
                              viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    print("URL:", page.url[:170], flush=True)
    print("BODY:", page.inner_text("body")[:260].replace("\n", " | "), flush=True)
    print("SLIDER ELS:", json.dumps(page.eval_on_selector_all(
        "body *",
        """els => els.filter(e => /slide|slider|nc_|captcha|punish/i.test(String(e.className)+' '+String(e.id)))
             .slice(0,25).map(e => { const r = e.getBoundingClientRect();
               return {tag:e.tagName, cls:String(e.className).slice(0,80), id:e.id,
                       txt:(e.textContent||'').trim().slice(0,45),
                       rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]}; })"""),
        ensure_ascii=False), flush=True)

    if "punish" in page.url or "滑块" in page.inner_text("body"):
        print("--- trying slider drag ---", flush=True)
        box = None
        for sel in ["#nc_1_n1z", ".btn_slide", ".nc_iconfont.btn_slide",
                    "[class*='slide']", "[id*='n1z']"]:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    box = loc.bounding_box()
                    print("slider selector:", sel, box, flush=True)
                    break
            except Exception:
                continue
        if box:
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            for i in range(1, 28):
                page.mouse.move(x + i * 11, y + ((i % 3) - 1) * 0.7)
                page.wait_for_timeout(22)
            page.mouse.up()
            page.wait_for_timeout(4000)
            print("AFTER URL:", page.url[:170], flush=True)
            print("AFTER BODY:", page.inner_text("body")[:260].replace("\n", " | "), flush=True)
            ctx.storage_state(path=STATE)
            print("state refreshed", flush=True)
        else:
            print("no slider element found", flush=True)
    else:
        print("NO CAPTCHA - page is normal", flush=True)
    browser.close()
