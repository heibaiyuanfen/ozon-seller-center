# -*- coding: utf-8 -*-
"""验证在图搜结果页内换图上传能否刷新结果（绕开 punish 搜索页）。"""
import glob
import json
import os

from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "1688_storage_state.json")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
IMGDIR = os.path.join(BASE, "sample_images")
AIR_URL = ("https://air.1688.com/kapp/1688-search/pc-image-search/"
           "?tab=imageSearch&imageId=1796808885482170577"
           "&imageIdList=1796808885482170577")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

TITLES_JS = "els => els.slice(0,3).map(e => (e.innerText||'').split('\\n')[0].slice(0,60))"

files = sorted(glob.glob(os.path.join(IMGDIR, "full_*.png")))
print("cached full images:", [os.path.basename(f) for f in files], flush=True)
new_img = None
for f in files:
    if "1898568498" not in f:
        new_img = f
        break
if new_img is None:
    new_img = os.path.join(IMGDIR, "ozon_full_1898568498.png")
print("upload:", new_img, flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=CHROME, headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(storage_state=STATE, user_agent=UA, locale="zh-CN",
                              viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto(AIR_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("div.searchOfferWrapper--St2OXxYo", state="attached", timeout=30000)
    print("URL0:", page.url[:150], flush=True)
    print("BEFORE:", json.dumps(page.eval_on_selector_all(
        "div.searchOfferWrapper--St2OXxYo", TITLES_JS), ensure_ascii=False), flush=True)

    page.set_input_files("input[type=file].image-file-reader-wrapper", new_img, timeout=20000)
    page.wait_for_timeout(3500)
    print("AFTER UPLOAD URL:", page.url[:150], flush=True)
    print("BODY HEAD:", page.inner_text("body")[:320].replace("\n", " | "), flush=True)
    print("搜索图片 btn:", page.get_by_text("搜索图片", exact=True).count(), flush=True)
    print("搜索 btn:", page.get_by_text("搜索", exact=True).count(), flush=True)

    clicked = False
    if page.get_by_text("搜索图片", exact=True).count():
        page.get_by_text("搜索图片", exact=True).first.click(timeout=12000)
        clicked = True
        print("CLICKED 搜索图片", flush=True)
    elif page.get_by_text("搜索", exact=True).count():
        page.get_by_text("搜索", exact=True).first.click(timeout=12000)
        clicked = True
        print("CLICKED 搜索", flush=True)
    print("clicked:", clicked, flush=True)

    page.wait_for_timeout(9000)
    print("FINAL URL:", page.url[:170], flush=True)
    print("AFTER:", json.dumps(page.eval_on_selector_all(
        "div.searchOfferWrapper--St2OXxYo", TITLES_JS), ensure_ascii=False), flush=True)
    print("card count:", page.locator("div.searchOfferWrapper--St2OXxYo").count(), flush=True)
    browser.close()
