# -*- coding: utf-8 -*-
"""打开有头 Chrome，等待真人拖动滑块过 1688 验证，随后保存会话状态。"""
import os
import time
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(BASE, "chrome_profile_1688")
STATE = os.path.join(BASE, "1688_storage_state.json")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SEARCH_URL = "https://s.1688.com/selloffer/offer_search.htm?keywords=" + quote("清洁刷")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE, executable_path=CHROME, headless=False,
        viewport={"width": 1360, "height": 900}, locale="zh-CN",
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print("goto warn:", type(e).__name__, str(e)[:120], flush=True)
    page.wait_for_timeout(2000)
    cur = page.url
    try:
        txt = page.inner_text("body")[:400]
    except Exception:
        txt = ""
    need = "punish" in cur or "滑块" in txt
    print("URL:", cur[:150], flush=True)
    print("NEED_CAPTCHA:", need, flush=True)
    if need:
        print(">>> 请在弹出的 Chrome 窗口里按住滑块拖到最右边 <<<", flush=True)

    deadline = time.time() + 300
    ok = False
    while time.time() < deadline:
        try:
            has_input = page.locator("input[type=file].image-file-reader-wrapper").count() > 0
            if "punish" not in page.url and has_input:
                ok = True
                break
        except Exception:
            pass
        time.sleep(3)

    print("PASSED:", ok, "final url:", page.url[:150], flush=True)
    if ok:
        ctx.storage_state(path=STATE)
        print("STATE SAVED:", STATE, flush=True)
    else:
        print("TIMEOUT - captcha not cleared", flush=True)
    ctx.close()
