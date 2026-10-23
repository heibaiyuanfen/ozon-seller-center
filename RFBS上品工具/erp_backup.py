"""Durable, credential-free ERP outbox, separate from platform submission."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import requests

from core import atomic_write_json, load_json

_LOCK = threading.RLock()


def base_url(value):
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path or (parsed.scheme != "https" and not
            (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}))):
        raise ValueError("产品库须使用根地址；仅本机允许 HTTP，远程必须 HTTPS")
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


def money(value):
    if value is None or str(value).strip() == "":
        return None
    number = Decimal(str(value))
    if not number.is_finite() or not 0 <= number <= Decimal("1000000000000") or number != number.quantize(Decimal('.0001')):
        raise ValueError("产品库金额须为非负数且最多四位小数")
    return format(number, 'f')


def no_credentials(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower().replace('-', '_') in {"authorization", "cookie", "password", "api_key", "access_key", "access_key_id", "access_key_secret", "token", "client_secret", "erp_access_token"}:
                raise ValueError("产品备份中禁止包含凭据字段")
            no_credentials(item)
    elif isinstance(value, list):
        for item in value:
            no_credentials(item)


@contextmanager
def outbox_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with _LOCK, open(root / 'outbox.lock', 'a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            deadline = time.monotonic() + 30
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('等待产品库备份锁超时')
                    time.sleep(.1)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def product_snapshot(submitted, ledger, definitions, identity):
    item = submitted['item']
    offer = str(item['offer_id'])
    entries = submitted.get('import_result', {}).get('result', {}).get('items', [])
    success = next((entry for entry in entries if str(entry.get('offer_id')) == offer
                    and str(entry.get('status')).lower() == 'imported' and not entry.get('errors')), None)
    if not submitted.get('submitted') or not success:
        raise ValueError('当前货号没有明确成功的 Ozon 导入回执，不能备份')
    product_id = success.get('product_id') or ledger.get('ozon_product_id')
    if not product_id or str(product_id) == '0':
        raise ValueError('成功回执缺少平台 product_id，请补查后重试产品库备份')
    urls = [item.get('primary_image'), *item.get('images', [])]
    urls = [url for url in urls if url]
    if len(set(urls)) != len(urls):
        raise ValueError('提交记录包含重复图片，须核对后备份，不能改变原提交图片顺序')
    if not 1 <= len(urls) <= 15:
        raise ValueError('产品库每个规格需要 1–15 张已上传图片')
    assets = []
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query or parsed.port not in (None, 443):
            raise ValueError('产品库图片必须是无认证参数的公开 HTTPS 链接')
        assets.append({'id': hashlib.sha256(url.encode()).hexdigest(), 'url': url})
    definition_map = {int(d['id']): d for d in definitions if d.get('id')}
    def attribute(entry):
        definition = definition_map.get(int(entry['id']), {})
        values = []
        for value in entry.get('values', []):
            converted = {'value': str(value.get('value', ''))}
            if int(value.get('dictionary_value_id') or 0) > 0:
                converted['dictionary_value_id'] = int(value['dictionary_value_id'])
            values.append(converted)
        dictionary = bool(values) and all('dictionary_value_id' in v for v in values)
        return dict(id=int(entry['id']), name=str(definition.get('name') or entry['id'])[:200],
                    value_type='string', required=bool(definition.get('is_required')), unit=None,
                    is_dictionary=dictionary, is_collection=len(values) > 1, values=values,
                    definition_snapshot=None)
    content = submitted.get('content') or {}
    tags = content.get('tags_ru') or []
    if not isinstance(tags, list):
        tags = str(tags).split()
    def measurement(key, factors):
        raw = item.get(key)
        if raw is None:
            return None
        unit = item.get('weight_unit' if key == 'weight' else 'dimension_unit')
        if unit not in factors:
            raise ValueError('产品库无法转换尺寸或重量单位')
        number = Decimal(str(raw)) * Decimal(str(factors[unit]))
        if not number.is_finite() or not 0 < number <= 1000000000:
            raise ValueError('产品库尺寸重量必须为有效正数')
        return int(number) if number == int(number) else float(number)
    variant = dict(external_variant_id=identity['variant_id'], sku=offer,
        specification=str(ledger.get('model_name') or offer), barcode=None,
        content=dict(language='ru', title=str(item['name']), description=content.get('description_ru'),
                     tags=list(dict.fromkeys(tags)), brand=None, rich_content=None),
        supplier=dict(name='1688' if ledger.get('supplier_1688_url') else None, product_id=None,
                      sku_id=None, url=ledger.get('supplier_1688_url') or None),
        cost=dict(currency='CNY', purchase=money(ledger.get('purchase_cost')), domestic_delivery=None,
                  other_per_unit=money(ledger.get('label_fee'))),
        package=dict(length_mm=measurement('depth', {'mm': 1, 'cm': 10, 'm': 1000}),
                     width_mm=measurement('width', {'mm': 1, 'cm': 10, 'm': 1000}),
                     height_mm=measurement('height', {'mm': 1, 'cm': 10, 'm': 1000}),
                     weight_g=measurement('weight', {'g': 1, 'kg': 1000}),
                     dimensions_source='unknown', weight_source='unknown'),
        category=dict(platform='ozon', description_category_id=int(item['description_category_id']),
                      type_id=int(item['type_id']), path=[p.strip() for p in str(ledger.get('category_path') or '').split('/') if p.strip()],
                      attributes_snapshot_at=None), attributes_complete=False,
        attributes=[attribute(a) for a in item.get('attributes', [])],
        complex_attributes=[dict(complex_id=int(group['attributes'][0]['complex_id']), group_index=index,
                                 attributes=[attribute(a) for a in group['attributes']])
                            for index, group in enumerate(item.get('complex_attributes', [])) if group.get('attributes')],
        images=[dict(asset_id=asset['id'], role='main' if index == 0 else 'additional', position=index)
                for index, asset in enumerate(assets)])
    listing = dict(external_listing_id=identity['listing_id'], external_variant_id=identity['variant_id'],
        platform='ozon', source_store=dict(external_store_id=str(ledger.get('shop_config_id') or ''), name=str(ledger.get('shop_name') or '')),
        offer_id=offer, product_id=str(product_id), platform_sku=str(success['sku']) if success.get('sku') else None,
        status='published', published_at=identity['published_at'],
        pricing=dict(currency=item['currency_code'], price=money(item['price']), old_price=money(item.get('old_price')), action_price=None),
        submitted_payload=copy.deepcopy(item))
    product = dict(external_product_id=identity['product_id'], revision=1, updated_at=identity['published_at'],
        sku=offer, group_code=identity['product_id'], source_type='follow', name=item['name'],
        source_url=ledger.get('ozon_source_url') if str(ledger.get('ozon_source_url') or '').startswith('https://') else None,
        variants=[variant], listings=[listing])
    no_credentials(product)
    return product, assets


class BackupOutbox:
    def __init__(self, root):
        self.root = Path(root)

    def prepare(self, submitted, ledger, definitions, locator, target):
        target = base_url(target)
        with outbox_lock(self.root):
            state_path = self.root / 'identity.json'
            state = load_json(state_path, {'namespace': uuid.uuid4().hex, 'records': {}})
            key = hashlib.sha256((target + '\n' + locator).encode()).hexdigest()
            identity = state['records'].setdefault(key, dict(product_id=uuid.uuid4().hex,
                variant_id=uuid.uuid4().hex, listing_id=uuid.uuid4().hex, published_at=now()))
            old_path = self.root / (identity.get('export_id', '') + '.json')
            if identity.get('export_id') and not old_path.with_suffix('.receipt.json').exists():
                return old_path
            product, assets = product_snapshot(submitted, ledger, definitions, identity)
            digest = hashlib.sha256(json.dumps([product, assets], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if identity.get('digest') == digest:
                return old_path
            revision = identity.get('revision', 0) + 1
            product.update(revision=revision, updated_at=now())
            document = dict(format='erp.follow-backup', schema_version='1.0.0', export_id=uuid.uuid4().hex,
                exported_at=now(), producer=dict(name='Ozon seller center', version='1.0.0', namespace=state['namespace']),
                assets=assets, products=[product])
            path = self.root / (document['export_id'] + '.json')
            atomic_write_json(path, document)
            atomic_write_json(path.with_suffix('.target.json'), {'base_url': target})
            identity.update(export_id=document['export_id'], digest=digest, revision=revision)
            atomic_write_json(state_path, state)
            return path

    def pending(self):
        return [p for p in self.root.glob('*.json') if len(p.stem) == 32 and not p.with_suffix('.receipt.json').exists()]


class ErpBackupClient:
    def __init__(self, url, token='', username='', password='', session=None, sleep=time.sleep):
        self.url = base_url(url)
        self.token, self.username, self.password = token, username, password
        self.session = session or requests.Session()
        self.sleep = sleep

    def call(self, method, path, **kwargs):
        for attempt in range(3):
            try:
                response = self.session.request(method, self.url + path, timeout=(5, 60), allow_redirects=False, **kwargs)
                if response.status_code == 200:
                    result = response.json()
                    if not isinstance(result, dict):
                        raise ValueError('产品库返回了非对象响应')
                    return result
                try:
                    error = response.json()
                except ValueError:
                    error = {}
                code = str(error.get('code') or 'HTTP_' + str(response.status_code))
                if (response.status_code >= 500 or code == 'backup_write_conflict') and attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"产品库请求失败：{code}；trace_id={error.get('trace_id', '')}；请核对权限、图片域名或版本冲突")
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 2:
                    raise RuntimeError('产品库连接失败或结果未知，已保留原 JSON，请原样重试') from None
                self.sleep(2 ** attempt)

    def login(self):
        config = self.call('GET', '/api/v1/auth/config')
        if config.get('multiuser'):
            if not self.username or not self.password:
                raise ValueError('请填写产品库个人账号和密码')
            self.call('POST', '/api/v1/auth/login', json=dict(username=self.username, password=self.password, remember=False))
        else:
            if not self.token:
                raise ValueError('请填写产品库本地访问密钥')
            self.session.headers['Authorization'] = 'Bearer ' + self.token

    def upload(self, path, log=lambda text: None):
        path = Path(path)
        target = load_json(path.with_suffix('.target.json'), {})
        if target.get('base_url') != self.url:
            raise ValueError('待上传记录属于另一个产品库地址，禁止跨库重试')
        receipt_path = path.with_suffix('.receipt.json')
        if receipt_path.exists():
            return load_json(receipt_path, {})
        data = path.read_bytes()
        if len(data) > 50 * 1024 * 1024:
            raise ValueError('产品库备份超过 50 MiB')
        document = json.loads(data)
        no_credentials(document)
        self.login()
        endpoint = '/api/v1/catalog/follow-backups/'
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        preview = self.call('POST', endpoint + 'validate', data=data, headers=headers)
        no_credentials(preview)
        atomic_write_json(path.with_suffix('.preview.json'), preview)
        log('产品库校验：' + json.dumps({'items': preview.get('items'), 'warnings': preview.get('warnings')}, ensure_ascii=False))
        if not preview.get('valid') or not (preview.get('can_import') or preview.get('replayed')):
            raise ValueError('产品库校验未通过或存在冲突，未导入；请查看校验回执')
        result = self.call('POST', endpoint + 'import', data=data, headers=headers)
        if result.get('imported') is not True or result.get('export_id') != document['export_id'] or not result.get('items') or any(not i.get('product_id') for i in result['items']):
            raise ValueError('产品库未返回可确认成功的导入回执，保留待重试')
        no_credentials(result)
        atomic_write_json(receipt_path, result)
        conflicts = [c for item in result.get('items', []) for c in item.get('field_conflicts', [])]
        log(('产品库已合并，存在资料差异待确认：' + json.dumps(conflicts, ensure_ascii=False)) if conflicts
            else '产品库备份成功（草稿资料；图片未验证，不代表新店铺已发布）')
        return result

    def close(self):
        self.session.close()
