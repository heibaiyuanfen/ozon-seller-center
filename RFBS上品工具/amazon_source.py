from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from core import USER_AGENT
from services import ImageDownloadService


ASIN_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{10})(?![A-Z0-9])", re.I)


@dataclass
class AmazonProduct:
    asin: str
    source_url: str
    title: str
    images: list[str]


def normalize_amazon_source(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("请输入亚马逊商品链接或 ASIN")
    if re.fullmatch(r"[A-Z0-9]{10}", raw, re.I):
        asin = raw.upper()
        return asin, f"https://www.amazon.com/dp/{asin}"
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = str(parsed.hostname or "").casefold()
    if not (host == "amazon.com" or host.endswith(".amazon.com") or ".amazon." in host):
        raise ValueError("请输入有效的亚马逊商品链接或 10 位 ASIN")
    match = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?#]|$)", parsed.path + "/", re.I)
    if not match:
        match = ASIN_RE.search(raw)
    if not match:
        raise ValueError("无法从亚马逊链接识别 10 位 ASIN")
    asin = match.group(1).upper()
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
    return asin, f"{scheme}://{parsed.netloc}/dp/{asin}"


def _json_ld(page: str):
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page, flags=re.I | re.S,
    ):
        try:
            value = json.loads(html.unescape(raw).strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        queue = value if isinstance(value, list) else [value]
        for item in queue:
            if isinstance(item, dict):
                yield item
                graph = item.get("@graph")
                if isinstance(graph, list):
                    queue.extend(graph)


def _decode_embedded_url(value: str) -> str:
    raw = str(value or "").replace(r"\/", "/")
    try:
        raw = json.loads('"' + raw.replace('"', r'\"') + '"')
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return html.unescape(raw).strip()


def parse_amazon_html(asin: str, url: str, page: str) -> AmazonProduct:
    product = next((
        item for item in _json_ld(page)
        if str(item.get("@type") or "").casefold() == "product"
    ), {})
    title = str(product.get("name") or "").strip()
    if not title:
        match = re.search(r'<span[^>]+id=["\']productTitle["\'][^>]*>(.*?)</span>', page, re.I | re.S)
        title = re.sub(r"<[^>]+>", " ", html.unescape(match.group(1))).strip() if match else ""
    candidates: list[str] = []
    raw_images = product.get("image") or []
    if not isinstance(raw_images, list):
        raw_images = [raw_images]
    candidates.extend(str(item) for item in raw_images)
    # Amazon embeds the ordered gallery in colorImages/landingImage data.
    for pattern in (
        r'["\']hiRes["\']\s*:\s*["\'](https?:\\?/\\?/[^"\']+)',
        r'["\']large["\']\s*:\s*["\'](https?:\\?/\\?/[^"\']+)',
        r'data-old-hires=["\'](https?://[^"\']+)',
        r'data-a-dynamic-image=["\'](.*?)["\']',
    ):
        for match in re.findall(pattern, page, re.I | re.S):
            if pattern.startswith("data-a-dynamic"):
                try:
                    dynamic = json.loads(html.unescape(match))
                    candidates.extend(str(key) for key in dynamic if str(key).startswith("http"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
            else:
                candidates.append(match)
    images = []
    seen = set()
    for candidate in candidates:
        image_url = _decode_embedded_url(candidate)
        if not image_url.startswith("http") or "media-amazon.com" not in image_url.casefold():
            continue
        # Remove Amazon's rendition suffix, retaining the original image object.
        image_url = re.sub(r"\._[A-Z0-9_,.-]+_\.(jpe?g|png|webp)$", r".\1", image_url, flags=re.I)
        key = image_url.casefold()
        if key not in seen:
            seen.add(key)
            images.append(image_url)
    if not images:
        raise RuntimeError("亚马逊页面没有返回可识别的商品图片")
    return AmazonProduct(asin=asin, source_url=url, title=title, images=images[:30])


def scrape_amazon_product(
    value: str, *, timeout=90, session=None, browser_profile_dir: str | Path | None = None,
) -> AmazonProduct:
    asin, url = normalize_amazon_source(value)
    client = session or requests.Session()
    direct_error = None
    try:
        response = client.get(url, headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        }, timeout=timeout)
        response.raise_for_status()
        return parse_amazon_html(asin, url, response.text)
    except Exception as error:
        direct_error = error
    if browser_profile_dir is None:
        raise RuntimeError(f"亚马逊页面未返回完整图库：{direct_error}") from direct_error
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("亚马逊直连解析失败，且未安装浏览器解析组件 playwright") from error
    profile = Path(browser_profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = None
        last_error = None
        for channel in ("msedge", "chrome"):
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(profile), channel=channel, headless=False, locale="en-US",
                    viewport={"width": 1360, "height": 900},
                    args=["--disable-blink-features=AutomationControlled"],
                )
                break
            except Exception as error:
                last_error = error
        if context is None:
            raise RuntimeError(f"无法启动 Edge/Chrome 解析亚马逊：{last_error}")
        try:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=min(timeout * 1000, 120000))
            except Exception:
                pass
            deadline = time.monotonic() + timeout
            last_parse_error = direct_error
            while time.monotonic() < deadline:
                if page.is_closed():
                    raise RuntimeError("浏览器窗口已关闭，未完成亚马逊商品解析")
                try:
                    page.wait_for_timeout(1500)
                    return parse_amazon_html(asin, page.url, page.content())
                except Exception as error:
                    last_parse_error = error
            raise RuntimeError(
                f"亚马逊页面在 {timeout} 秒内未加载完整图库；如出现验证码，请完成验证后重试：{last_parse_error}"
            )
        finally:
            context.close()


def download_amazon_images(
    product: AmazonProduct, root_dir: str | Path, *, limit=15, session=None, log_func=None,
) -> tuple[list[str], Path]:
    batch = time.strftime("%Y%m%d-%H%M%S")
    output_dir = Path(root_dir) / product.asin / batch
    downloader = ImageDownloadService(session=session)
    paths = downloader.download(product.images, str(output_dir), limit=limit, log_func=log_func)
    numbered: list[str] = []
    for index, raw_path in enumerate(paths, start=1):
        source = Path(raw_path)
        destination = output_dir / f"{index:02d}.jpg"
        if source != destination:
            source.replace(destination)
        numbered.append(str(destination))
    return numbered, output_dir
