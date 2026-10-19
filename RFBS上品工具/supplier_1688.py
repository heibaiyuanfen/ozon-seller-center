from __future__ import annotations

import copy
import hashlib
import io
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageOps

from core import atomic_write_json, load_json


def normalize_1688_url(value):
    raw = str(value).strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    match = re.search(r"/offer/(\d+)\.html", parsed.path)
    if not (host == "1688.com" or host.endswith(".1688.com")) or not match:
        raise ValueError("请输入 detail.1688.com/offer/商品ID.html 格式的商品链接")
    return f"https://detail.1688.com/offer/{match[1]}.html"


def original_1688_image(value):
    raw = str(value or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (host == "alicdn.com" or host.endswith(".alicdn.com")):
        return ""
    if not re.search(r"/(?:img/ibank|imgextra)/", parsed.path, re.I):
        return ""
    return re.sub(r"(\.(?:jpg|jpeg|png|webp))_[^/?]+", r"\1", raw, flags=re.I)


def collect_1688_images(url, destination, profile, log=lambda message: None, timeout=180):
    from playwright.sync_api import sync_playwright
    from browser_runtime import chromium_launch_candidates, browser_profile_lock
    url = normalize_1688_url(url)
    destination = Path(destination) / uuid.uuid4().hex
    destination.mkdir(parents=True, exist_ok=True)
    with browser_profile_lock(Path(profile)), sync_playwright() as playwright:
        context = None
        errors = []
        for options in chromium_launch_candidates():
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(profile), **options, headless=False, locale="zh-CN",
                    viewport={"width": 1360, "height": 900})
                break
            except Exception as error:
                errors.append(str(error))
        if context is None:
            raise RuntimeError("无法启动采集浏览器：" + "; ".join(errors))
        try:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except Exception as error:
                log(f"页面导航尚未完成，将继续等待：{type(error).__name__}")
            log("请在采集浏览器中完成 1688 登录或验证；可滚动查看详情以加载更多图片")
            deadline = time.monotonic() + timeout
            found = {}
            stable = 0
            title = ""
            while time.monotonic() < deadline:
                if page.is_closed():
                    raise RuntimeError("1688 采集窗口已关闭")
                try:
                    snapshot = page.evaluate('''() => ({
                        title: document.querySelector('h1')?.innerText ||
                          document.querySelector('meta[property="og:title"]')?.content || document.title,
                        images: [...document.images].map(img => ({
                          url: img.getAttribute('data-lazy-src') || img.getAttribute('data-src') || img.currentSrc || img.src,
                          label: img.alt || '',
                          group: img.closest('[class*="sku"],[class*="Sku"]') ? '规格' :
                            img.closest('[class*="description"],[class*="Description"],[class*="detail-desc"]') ? '详情' : '候选'
                        }))
                    })''')
                except Exception as error:
                    if "context was destroyed" not in str(error).lower():
                        raise
                    page.wait_for_timeout(1000)
                    continue
                previous = len(found)
                for item in snapshot["images"]:
                    candidate = original_1688_image(item["url"])
                    if candidate:
                        found.setdefault(candidate, {"url": candidate, "label": item["label"], "group": item["group"]})
                title = str(snapshot["title"] or "").strip()
                if found and not any(word in title for word in ("登录", "验证码", "安全验证")):
                    stable = stable + 1 if previous == len(found) else 0
                    if stable >= 6:
                        break
                    page.evaluate("window.scrollBy(0, Math.max(600, innerHeight * 0.8))")
                page.wait_for_timeout(1200)
            if not found:
                raise RuntimeError("未识别到商品图片；请确认已登录并能查看该 1688 商品")
            assets, seen, failures = [], set(), []
            for index, item in enumerate(list(found.values())[:80], 1):
                log(f"下载候选图 {index}/{min(len(found), 80)}")
                try:
                    response = context.request.get(item["url"], headers={"Referer": url}, timeout=25000)
                    if not response.ok:
                        raise RuntimeError(f"HTTP {response.status}")
                    body = response.body()
                    if len(body) > 30 * 1024 * 1024:
                        raise ValueError("图片超过 30MB")
                    digest = hashlib.sha256(body).hexdigest()
                    if digest in seen:
                        continue
                    with Image.open(io.BytesIO(body)) as image:
                        if min(image.size) < 100:
                            continue
                        image = ImageOps.exif_transpose(image).convert("RGB")
                        path = destination / f"{len(assets) + 1:03d}-{digest[:12]}.jpg"
                        image.save(path, quality=95)
                        assets.append({**item, "path": str(path), "width": image.width, "height": image.height})
                    seen.add(digest)
                except Exception as error:
                    failures.append(str(error))
            if not assets:
                raise RuntimeError("候选图片全部下载失败：" + "; ".join(failures[:3]))
            return {"url": url, "title": title, "assets": assets, "failures": len(failures)}
        finally:
            try:
                context.close()
            except Exception:
                pass


class SupplierWorkspace:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "workspace.json"
        self.data = load_json(self.path, {"products": []})
        if not isinstance(self.data, dict) or not isinstance(self.data.get("products"), list):
            raise ValueError("1688 工作区数据损坏，请保留 workspace.json 检查")

    def save(self):
        atomic_write_json(self.path, self.data)

    def ensure_identifiers(self, product, model_name=None):
        model = (model_name or product.get("model_name") or next(
            (v.get("model_name") for v in product["variants"] if v.get("model_name")),
            "1688-M-" + product["id"][:16].upper())).strip()
        product["model_name"] = model
        for variant in product["variants"]:
            variant["model_name"] = model
            if not str(variant.get("offer_id") or "").strip():
                variant["offer_id"] = self.new_offer_id()

    def new_offer_id(self):
        existing = {v.get("offer_id") for p in self.data["products"] for v in p["variants"]}
        while True:
            value = "1688-" + uuid.uuid4().hex[:16].upper()
            if value not in existing:
                return value

    def set_generated_main(self, product, target_ids, path):
        with Image.open(path) as image:
            image.load()
            width, height = image.size
        if width * 4 != height * 3 or min(width, height) < 600:
            raise ValueError("主图必须为 3:4，短边至少 600 像素")
        variants = [v for v in product["variants"] if v["id"] in target_ids]
        if len(variants) != len(set(target_ids)):
            raise ValueError("生成目标变体已改变，请重试")
        previous = copy.deepcopy(product)
        try:
            product["assets"].append({"path": str(path), "label": "AI主图", "group": "AI主图", "width": width, "height": height})
            for variant in variants:
                variant["images"] = [str(path), *[p for p in variant["images"][1:] if p != str(path)]]
            self.save()
        except Exception:
            product.clear()
            product.update(previous)
            raise

    def add_product(self, product):
        product = copy.deepcopy(product)
        product.update(id=uuid.uuid4().hex, variants=[])
        self.data["products"].append(product)
        self.add_variant(product)
        return product

    def add_variant(self, product, source=None):
        variant = copy.deepcopy(source) if source else {"name": "默认款", "images": [], "purchase_cost": "", "model_name": "", "weight": "", "depth": "", "width": "", "height": ""}
        variant["id"] = uuid.uuid4().hex
        variant["offer_id"] = self.new_offer_id()
        if source:
            variant["name"] += " 副本"
        product["variants"].append(variant)
        self.ensure_identifiers(product)
        self.save()
        return variant

    def delete_assets(self, product, indices):
        selected = set(int(index) for index in indices)
        if not selected:
            return 0
        if any(index < 0 or index >= len(product["assets"]) for index in selected):
            raise ValueError("素材选择已变化，请重新选择图片")
        previous = copy.deepcopy(product)
        removed = [asset for index, asset in enumerate(product["assets"]) if index in selected]
        product.setdefault("deleted_asset_batches", []).append(copy.deepcopy(removed))
        product["assets"] = [asset for index, asset in enumerate(product["assets"]) if index not in selected]
        try:
            self.save()
        except Exception:
            product.clear()
            product.update(previous)
            raise
        return len(removed)

    def restore_assets(self, product):
        batches = product.get("deleted_asset_batches", [])
        if not batches:
            return 0
        previous = copy.deepcopy(product)
        existing = {asset["path"] for asset in product["assets"]}
        restored = [asset for asset in batches.pop() if asset["path"] not in existing]
        product["assets"].extend(restored)
        try:
            self.save()
        except Exception:
            product.clear()
            product.update(previous)
            raise
        return len(restored)

    def edit_image(self, path, operation, box=None):
        self.root.mkdir(parents=True, exist_ok=True)
        folder = self.root / "edited"
        folder.mkdir(exist_ok=True)
        output = folder / (uuid.uuid4().hex + ".jpg")
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            if operation == "rotate":
                image = image.transpose(Image.Transpose.ROTATE_270)
            elif operation == "crop":
                if not box or not (0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height):
                    raise ValueError("裁剪坐标超出图片范围")
                image = image.crop(box)
            else:
                raise ValueError("未知图片编辑操作")
            image.convert("RGB").save(output, quality=95)
        return str(output)
