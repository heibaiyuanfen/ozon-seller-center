"""Reusable ERP product exchange client. Public read/create API; no account, password or API key required."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests
from catalog_contract import validate_product, validate_upload_capabilities, validate_receipt

__version__ = "1.2.0"


class CatalogRequestError(RuntimeError):
    def __init__(self, status_code=None, trace_id='', retry_at=None):
        self.status_code, self.trace_id, self.retry_at = status_code, trace_id, retry_at
        descriptions = {401: '核对专用接口路径和部署版本，无需登录',
                        403: '核对请求方法、来源限制或网关', 404: '产品不存在、已归档或接口地址不正确',
                        409: '来源身份内容冲突，人工核对原产品；不要更换来源身份',
                        422: '字段、长度、链接或数值不符合数据模型',
                        429: '请求受限，请等待 Retry-After 后原样重试'}
        message = (f'产品库请求失败 HTTP {status_code}；' + descriptions.get(status_code, '结果未知，保留原请求稍后原样重试')
                   if status_code is not None else '连接失败或结果未知；保留原始上传文件稍后原样重试')
        if trace_id:
            message += f'；trace_id={trace_id}'
        super().__init__(message)


def retry_after_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        delay = int(value)
        return time.time() + max(0, delay)
    except (ValueError, OverflowError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(time.time(), date.timestamp())
        except (ValueError, TypeError, OverflowError):
            return None


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
        self._capability = None

    def call(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.base_url + path, timeout=self.timeout, allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise CatalogRequestError() from None
        if response.status_code != 200:
            # Do not echo response bodies, URLs or headers that may contain secrets.
            try:
                body = response.json()
                trace = body.get('trace_id', '') if isinstance(body, dict) else ''
                trace = trace if isinstance(trace, str) and re.fullmatch(r'[a-zA-Z0-9._:-]{1,128}', trace) else ''
            except (ValueError, TypeError):
                trace = ''
            raise CatalogRequestError(response.status_code, trace,
                                      retry_after_timestamp(response.headers.get('Retry-After')) if response.status_code == 429 else None)
        try:
            value = response.json()
        except ValueError:
            raise RuntimeError('产品库返回了非JSON数据，保留原请求核对服务地址') from None
        if not isinstance(value, dict):
            raise RuntimeError("产品库返回格式错误")
        return value

    def connect(self):
        self._capability = self.call("GET", "/api/v1/shared-catalog/v1/capabilities")
        return self._capability

    def login(self, username=None, password=None):
        """Compatibility alias; supplied credentials are ignored and never sent."""
        return self.connect()

    def search(self, search="", after_id="", limit=50):
        if not isinstance(search, str) or len(search) > 200 or not isinstance(after_id, str) or len(after_id) > 32:
            raise ValueError('搜索关键词最多200字符，游标最多32字符；换关键词时请清空游标')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('分页数量须为1–100的整数')
        return self.call("GET", "/api/v1/shared-catalog/v1/products", params=dict(search=search, after_id=after_id, limit=limit))

    def read(self, product_id):
        if not isinstance(product_id, str) or len(product_id) != 32 or any(c not in "0123456789abcdef" for c in product_id):
            raise ValueError("产品 ID 无效")
        return self.call("GET", "/api/v1/shared-catalog/v1/products/" + product_id)

    def add(self, product):
        validate_product(product)
        validate_upload_capabilities(self._capability or self.connect())
        result = self.call("POST", "/api/v1/shared-catalog/v1/products", json=product)
        validate_receipt(result)
        return result

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
