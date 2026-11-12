# -*- coding: utf-8 -*-
"""探测：直接打开图搜结果页换图上传，绕开被 punish 的 s.1688.com 搜索页。"""
import json
import os

from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "1688_storage_state.json")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
AIR_URL = ("https://air.1688.com/kapp/1688-search/pc-image-search/"
           "?tab=imageSearch&imageId=1796808885482170577"
           "&imageIdList=1796808885482170577")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=CHROME, headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(storage_state=STATE, user_agent=UA, locale="zh-CN",
                              viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(AIR_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print("goto warn:", type(e).__name__, str(e)[:150], flush=True)
    page.wait_for_timeout(6000)
    print("URL:", page.url[:170], flush=True)
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""
    print("bodylen:", len(body), "| head:", body[:360].replace("\n", " | "), flush=True)
    print("punish:", "punish" in page.url or "滑块" in body[:400], flush=True)
    try:
        print("cards:", page.locator("div.searchOfferWrapper--St2OXxYo").count(), flush=True)
    except Exception as e:
        print("cards err", str(e)[:100], flush=True)
    print("file inputs:", json.dumps(page.eval_on_selector_all(
        "input[type=file]",
        "els => els.map(e => ({cls:String(e.className).slice(0,80), accept:e.accept}))"),
        ensure_ascii=False), flush=True)
    print("buttons:", json.dumps(page.eval_on_selector_all(
        "body *",
        """els => els.filter(e => e.children.length === 0 &&
              /^(上传图片|搜索图片|搜索|搜 索|找相似|严格同款)$/.test((e.textContent||'').trim()))
             .slice(0,12).map(e => ({tag:e.tagName, cls:String(e.className).slice(0,60),
                                     txt:(e.textContent||'').trim()}))"""),
        ensure_ascii=False), flush=True)
    browser.close()
