"""Reusable ERP product exchange client. Public read/create API; no account, password or API key required."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import requests

__version__ = "1.1.0"


class CatalogClient:
    def __init__(self, base_url, session=None):
        base_url = base_url.rstrip("/")
        url = urlsplit(base_url)
        if (not url.hostname or url.username or url.password or url.path or url.query or url.fragment
                or not (url.scheme == "https" or (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}))):
            raise ValueError("请使用 HTTPS 根地址；只有本机可以使用 HTTP")
        self.base_url = base_url
        self.session = session or requests.Session()
        self.timeout = (5, 20)  # Data delivery is optional; keep listing-worker waits bounded.

    def call(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.base_url + path, timeout=self.timeout, allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise RuntimeError("连接失败或结果未知；请保留来源编号和原始上传文件，重试时不要修改") from None
        if response.status_code != 200:
            # Do not echo response bodies, URLs or headers that may contain secrets.
            raise RuntimeError(f"产品库请求失败 HTTP {response.status_code}；401/403 请核对服务地址和部署版本，409 核对资料差异，422 检查字段")
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("产品库返回格式错误")
        return value

    def connect(self):
        return self.call("GET", "/api/v1/shared-catalog/v1/capabilities")

    def login(self, username=None, password=None):
        """Compatibility alias; supplied credentials are ignored and never sent."""
        return self.connect()

    def search(self, search="", after_id="", limit=50):
        return self.call("GET", "/api/v1/shared-catalog/v1/products", params=dict(search=search, after_id=after_id, limit=limit))

    def read(self, product_id):
        if len(product_id) != 32 or any(c not in "0123456789abcdef" for c in product_id):
            raise ValueError("产品 ID 无效")
        return self.call("GET", "/api/v1/shared-catalog/v1/products/" + product_id)

    def add(self, product):
        return self.call("POST", "/api/v1/shared-catalog/v1/products", json=product)

    def close(self):
        self.session.close()


def from_ozon_submission(document, source_store_id):
    """Explicitly selected seller-center submission -> reusable product facts.

    Shop ID must be the original stable shop identity. Never infer it from SKU
    or the currently selected shop: old submitted-{offer}.json may be overwritten.
    """
    if not source_store_id.strip():
        raise ValueError("请指定原提交所属店铺的稳定 ID，并核对原提交文件")
    item = document.get("item") or {}
    offer = str(item.get("offer_id") or "").strip()
    entries = (document.get("import_result") or {}).get("result", {}).get("items", [])
    confirmed = [r for r in entries if str(r.get("offer_id")) == offer and r.get("status") == "imported" and not r.get("errors")]
    if not document.get("submitted") or not offer or len(confirmed) != 1:
        raise ValueError("所选文件缺少本货号明确成功的导入回执")
    def measure(key, units, unit_key):
        raw = item.get(key)
        if raw is None:
            return None
        unit = item.get(unit_key)
        if unit not in units:
            raise ValueError("尺寸或重量单位未知，请核对原文件")
        return float(raw) * units[unit]
    images = list(dict.fromkeys(url for url in [item.get("primary_image"), *(item.get("images") or [])] if url))
    variant = dict(sku=offer, specification="", images=images,
                   package_length_mm=measure("depth", {"mm": 1, "cm": 10, "m": 1000}, "dimension_unit"),
                   package_width_mm=measure("width", {"mm": 1, "cm": 10, "m": 1000}, "dimension_unit"),
                   package_height_mm=measure("height", {"mm": 1, "cm": 10, "m": 1000}, "dimension_unit"),
                   weight_grams=measure("weight", {"g": 1, "kg": 1000}, "weight_unit"))
    # No platform sale price used as purchase cost; missing facts remain empty.
    identity = hashlib.sha256(json.dumps([source_store_id.strip(), offer], ensure_ascii=False).encode()).hexdigest()
    return dict(source="ozon-seller-center", external_id=identity, sku=offer, name=item.get("name") or "",
                description_category_id=item.get("description_category_id"), type_id=item.get("type_id"), variants=[variant])


def main():
    parser = argparse.ArgumentParser(description="共享产品库：读取 / 写入；不会调用 OZON 或执行上架")
    parser.add_argument("--url", required=True)
    parser.add_argument("--username", default="", help=argparse.SUPPRESS)  # old CLI compatibility; ignored
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search")
    search.add_argument("--text", default="")
    search.add_argument("--after-id", default="")
    read = sub.add_parser("read")
    read.add_argument("product_id")
    read.add_argument("--output", required=True)
    upload = sub.add_parser("upload")
    upload.add_argument("file")
    convert = sub.add_parser("prepare-ozon")
    convert.add_argument("file")
    convert.add_argument("--source-store-id", required=True)
    convert.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "prepare-ozon":
        value = from_ozon_submission(json.loads(Path(args.file).read_text(encoding="utf-8-sig")), args.source_store_id)
        with Path(args.output).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print("入库文件已生成，请核对后执行 upload；尚未上传。")
        return
    client = CatalogClient(args.url)
    try:
        client.connect()
        if args.command == "search":
            value = client.search(args.text, args.after_id)
        elif args.command == "read":
            value = client.read(args.product_id)
            with Path(args.output).open("x", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
            print("产品资料已导出；需在上架程序中核对类目、生成新文案并确认提交。")
            return
        else:
            value = client.add(json.loads(Path(args.file).read_text(encoding="utf-8-sig")))
        print(json.dumps(value, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
