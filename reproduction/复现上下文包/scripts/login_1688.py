# -*- coding: utf-8 -*-
"""打开独立有头 Chrome 让用户登录 1688，保存会话并验证搜索页可读性。"""
import json
import os
import time
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(BASE, "chrome_profile_1688")
STATE = os.path.join(BASE, "1688_storage_state.json")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
LOGIN_HINT = os.path.join(BASE, "1688_login_window.png")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE,
        executable_path=CHROME,
        headless=False,
        viewport={"width": 1360, "height": 900},
        locale="zh-CN",
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto("https://www.1688.com/", wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print("goto warn:", type(e).__name__, str(e)[:120], flush=True)
    print("WINDOW OPEN - please sign in to 1688 in the Chrome window", flush=True)
    try:
        page.screenshot(path=LOGIN_HINT)
        print("login window screenshot:", LOGIN_HINT, flush=True)
    except Exception as e:
        print("screenshot warn:", str(e)[:120], flush=True)

    logged = False
    deadline = time.time() + 300  # 最多等 5 分钟
    while time.time() < deadline:
        try:
            names = {c["name"] for c in ctx.cookies()}
            hit = sorted(names & {"unb", "_nk_", "tracknick", "lgc", "havana_lgc", "sgcookie"})
            cur = page.url
            if hit and "login.taobao.com" not in cur and "login.1688.com" not in cur:
                print("LOGIN DETECTED cookies:", hit, "url:", cur[:100], flush=True)
                logged = True
                break
        except Exception:
            pass
        time.sleep(3)

    print("logged:", logged, flush=True)
    if logged:
        ctx.storage_state(path=STATE)
        print("storage state saved:", STATE, flush=True)
        kw = "7合1 清洁套装 键盘清洁刷"
        page2 = ctx.new_page()
        try:
            page2.goto(
                "https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote(kw),
                wait_until="domcontentloaded", timeout=60000)
            page2.wait_for_timeout(8000)
            body = page2.inner_text("body")
            print("SEARCH url:", page2.url[:140], flush=True)
            print("SEARCH body len:", len(body), flush=True)
            print("SEARCH body head:", body[:1200].replace("\n", " | "), flush=True)
            links = page2.eval_on_selector_all(
                "a[href*='detail.1688.com/offer']", "els => els.map(e => e.href)")
            print("SEARCH offer links:", json.dumps(list(dict.fromkeys(links))[:10], ensure_ascii=False), flush=True)
            imgs = page2.eval_on_selector_all(
                "img", "els => els.map(e => e.src).filter(s => s && s.indexOf('alicdn') > -1)")
            print("SEARCH alicdn imgs:", json.dumps(imgs[:8], ensure_ascii=False), flush=True)
            page2.screenshot(path=os.path.join(BASE, "1688_after_login.png"))
            print("search screenshot saved", flush=True)
        except Exception as e:
            print("SEARCH ERR", type(e).__name__, str(e)[:200], flush=True)
    else:
        print("TIMEOUT - no login detected within 5 minutes", flush=True)
    ctx.close()
