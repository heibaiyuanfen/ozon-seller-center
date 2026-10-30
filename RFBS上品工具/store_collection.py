"""Extract public storefront product links without assuming a specific card layout."""
import re
import time
from urllib.parse import urljoin, urlsplit
from pathlib import Path
from html.parser import HTMLParser


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            self.links.extend(value for name, value in attrs if name.lower() == 'href' and value)


def product_links_from_html(content):
    parser = _LinkParser()
    parser.feed(content)
    return product_links(parser.links)


def product_links(hrefs, base_url='https://www.ozon.ru/'):
    result = []
    seen = set()
    for href in hrefs:
        parts = urlsplit(urljoin(base_url, str(href or '')))
        if parts.scheme not in ('http', 'https') or parts.hostname not in ('ozon.ru', 'www.ozon.ru'):
            continue
        match = re.fullmatch(r'/product/(?:[^/]*-)?(\d{6,})/?', parts.path)
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            result.append('https://www.ozon.ru/product/' + match.group(1) + '/')
    return result


def collect_visible_store(url, profile, on_progress=None, rounds=20):
    from playwright.sync_api import sync_playwright
    from browser_runtime import browser_profile_lock, chromium_launch_candidates, evaluate_after_navigation
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('ozon.ru', 'www.ozon.ru') or not parsed.path.startswith('/seller/'):
        raise ValueError('请输入 https://www.ozon.ru/seller/ 开头的店铺链接')
    profile = Path(profile)
    seen = {}
    with browser_profile_lock(profile), sync_playwright() as playwright:
        context = None
        failures = []
        candidates = [{'channel': 'chrome'}] + [candidate for candidate in chromium_launch_candidates()
                                                if candidate.get('channel') != 'chrome']
        for candidate in candidates:
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(profile), **candidate, headless=False, locale='ru-RU',
                    viewport={'width': 1360, 'height': 900})
                break
            except Exception as error:
                failures.append(str(error))
        if context is None:
            raise RuntimeError('无法启动采集浏览器：' + '; '.join(failures))
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
            for iteration in range(max(1, min(int(rounds), 100))):
                if page.is_closed():
                    raise RuntimeError('采集浏览器已关闭，已采集结果未保存')
                page.wait_for_timeout(2000)
                try:
                    cards = evaluate_after_navigation(page,
                        "() => Array.from(document.querySelectorAll('a[href*=\"/product/\"]')).map(n => ({href:n.href, title:(n.innerText || n.getAttribute('title') || '').trim()}))",
                        time.monotonic() + 30)
                except Exception:
                    if seen:
                        break
                    raise
                if not cards:
                    body = evaluate_after_navigation(page, '() => document.body ? document.body.innerText : ""', time.monotonic() + 30)
                    if 'Похоже, нет соединения' in body:
                        raise RuntimeError('Ozon 返回连接异常页面。普通 Chrome 可访问不代表独立采集会话可访问；请停止频繁重试，在采集窗口中检查登录、验证和网络，或联系 Ozon 支持。')
                for card in cards:
                    links = product_links([card.get('href')])
                    if links:
                        seen.setdefault(links[0], str(card.get('title') or '')[:200])
                if on_progress:
                    on_progress(iteration + 1, len(seen))
                try:
                    evaluate_after_navigation(page, '() => window.scrollTo(0, document.body.scrollHeight)', time.monotonic() + 30)
                except Exception:
                    if seen:
                        break
                    raise
            return [{'url': url, 'title': title} for url, title in seen.items()]
        finally:
            context.close()
