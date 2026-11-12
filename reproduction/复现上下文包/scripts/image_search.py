# -*- coding: utf-8 -*-
"""用 OZON 商品高清图上传 1688「以图搜款」，验证能否返回同款货源。"""
import json
import os
import re
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "1688_storage_state.json")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
FULL_IMG = os.path.join(BASE, "sample_images", "ozon_full_1898568498.png")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CARDS_JS = """els => els.map(a => ({
    href: a.href,
    text: (a.innerText || '').trim(),
    img: (a.querySelector('img') || {}).src || ''
})).filter(x => x.text.length > 8)"""


def summarize(cards):
    out = []
    for c in cards[:12]:
        flat = re.sub(r"\s+", "", c["text"])
        m = re.search(r"¥([\d.]+)", flat)
        price = m.group(1) if m else ""
        mf = re.search(r"运费([\d.]+)元", flat)
        freight = mf.group(1) if mf else ("0(包邮)" if "包邮" in flat else "")
        title = c["text"].split("\n")[0][:60]
        out.append({"title": title, "price": price, "freight": freight,
                    "offer": re.search(r"/offer/(\d+)", c["href"]).group(1)
                    if re.search(r"/offer/(\d+)", c["href"]) else "",
                    "img": c["img"][:110]})
    return out


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
    print("start url:", page.url[:120], flush=True)

    before = list(ctx.pages)
    try:
        page.set_input_files("input[type=file].image-file-reader-wrapper", FULL_IMG, timeout=20000)
        print("file uploaded", flush=True)
    except Exception as e:
        print("UPLOAD ERR", type(e).__name__, str(e)[:250], flush=True)

    page.wait_for_timeout(3500)
    print("search buttons:", json.dumps(page.eval_on_selector_all(
        "body *",
        """els => els.filter(e => e.children.length === 0 && (e.textContent||'').trim() === '搜索图片')
             .map(e => ({tag:e.tagName, cls:String(e.className).slice(0,80), outer:e.outerHTML.slice(0,220)}))"""),
        ensure_ascii=False), flush=True)
    try:
        page.get_by_text("搜索图片", exact=True).first.click(timeout=12000)
        print("clicked 搜索图片", flush=True)
    except Exception as e:
        print("CLICK ERR", type(e).__name__, str(e)[:250], flush=True)

    page.wait_for_timeout(12000)
    newpages = [pg for pg in ctx.pages if pg not in before]
    target = newpages[0] if newpages else page
    print("pages:", len(ctx.pages), "using new page:", bool(newpages), flush=True)
    print("result url:", target.url[:200], flush=True)
    try:
        target.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    try:
        body = target.inner_text("body")
        print("body len:", len(body), flush=True)
        print("body head:", body[:900].replace("\n", " | "), flush=True)
        cards = target.eval_on_selector_all("a[href*='detail.1688.com/offer']", CARDS_JS)
        print("cards:", len(cards), flush=True)
        print("CANDIDATES:", json.dumps(summarize(cards), ensure_ascii=False), flush=True)
        target.screenshot(path=os.path.join(BASE, "1688_image_search.png"))
        print("screenshot saved", flush=True)
    except Exception as e:
        print("RESULT ERR", type(e).__name__, str(e)[:250], flush=True)
    browser.close()
