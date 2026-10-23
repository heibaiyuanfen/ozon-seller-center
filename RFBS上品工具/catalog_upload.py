"""Public catalog v1 data-only uploads; independent durable retry, never platform publishing."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from core import atomic_write_json, load_json
from erp_backup import outbox_lock
from shared_catalog_client import CatalogClient


def public_url(value):
    value = str(value or '').strip()
    parsed = urlsplit(value)
    if value and (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('链接须为无认证信息、查询串及片段的公开 HTTPS 地址')
    return value


def numeric(value, positive=False):
    if value is None or str(value).strip() == '':
        return None
    n = Decimal(str(value))
    if not n.is_finite() or n < 0 or (positive and n == 0):
        raise ValueError('成本须非负、尺寸重量须为正数；未知值请留空')
    return float(n)


def product_data(row, namespace, images=()):
    shop, offer = str(row.get('shop_config_id') or '').strip(), str(row.get('offer_id') or '').strip()
    name = str(row.get('title') or '').strip()
    if not shop or not offer or not name:
        raise ValueError('缺少原始店铺配置ID、货号或商品标题，无法建立稳定来源身份')
    if len(offer) > 200 or len(name) > 600:
        raise ValueError('货号或标题超过接口长度限制')
    identity = hashlib.sha256(json.dumps([namespace, 'Ozon', shop, offer], ensure_ascii=False).encode()).hexdigest()
    urls = list(dict.fromkeys(public_url(url) for url in images if url))
    if len(urls) > 15:
        raise ValueError('最多 15 张图片链接')
    variant = dict(sku=offer, specification=str(row.get('model_name') or '')[:500],
        purchase_price_cny=numeric(row.get('purchase_cost')),
        package_length_mm=numeric(row.get('depth_mm'), True), package_width_mm=numeric(row.get('width_mm'), True),
        package_height_mm=numeric(row.get('height_mm'), True), weight_grams=numeric(row.get('gross_weight_g'), True), images=urls)
    product = dict(source='ozon-seller-center', external_id=identity, sku=offer, name=name, variants=[variant])
    source = row.get('supplier_1688_url') or row.get('ozon_source_url')
    if source:
        product['source_url'] = public_url(source)
    for key, field in (('description_category_id', 'category_id'), ('type_id', 'type_id')):
        if row.get(field) not in (None, ''):
            value = int(str(row[field]))
            if value <= 0:
                raise ValueError('类目与类型 ID 须为正整数')
            product[key] = value
    return product


class CatalogOutbox:
    def __init__(self, root):
        self.root = Path(root) / 'shared-catalog-outbox'

    def records(self):
        records = []
        for path in self.root.glob('*.request.json'):
            try:
                request = load_json(path, {})
                receipt = load_json(path.with_name(path.name.replace('.request.json', '.receipt.json')), {})
                status = load_json(path.with_name(path.name.replace('.request.json', '.status.json')), {})
                product_id = receipt.get('product_id', '')
                valid = isinstance(product_id, str) and len(product_id) == 32 and all(c in '0123456789abcdef' for c in product_id)
                records.append(dict(target=request.get('target', ''), sku=request.get('product', {}).get('sku', ''),
                    status='上传成功（有服务端回执）' if valid else status.get('state', '待上传 / 结果未确认'),
                    product_id=product_id if valid else '', time=status.get('time', ''), detail=status.get('detail', '')))
            except Exception:
                records.append(dict(target='', sku=path.stem, status='本地记录读取失败', product_id='', time='', detail='请检查记录文件'))
        for path in self.root.glob('*.failure.json'):
            try:
                records.append(load_json(path, {}))
            except Exception:
                pass
        return sorted(records, key=lambda row: row.get('time', ''), reverse=True)

    def prepare(self, row, url, images=()):
        # Validate the endpoint without issuing a request.
        parsed = CatalogClient(url)
        target = parsed.base_url
        parsed.close()
        with outbox_lock(self.root):
            namespace_path = self.root / 'namespace.json'
            state = load_json(namespace_path, {})
            if not state.get('namespace'):
                state = {'namespace': uuid.uuid4().hex}
                atomic_write_json(namespace_path, state)
            product = product_data(row, state['namespace'], images)
            key = hashlib.sha256((target + product['external_id']).encode()).hexdigest()
            path = self.root / (key + '.request.json')
            existing = load_json(path, {})
            if existing and existing != {'target': target, 'product': product}:
                raise ValueError('同一来源的资料已经变化；原始上传请求保留，请核对已入库产品，不自动换ID重传')
            if not existing:
                atomic_write_json(path, {'target': target, 'product': product})
            return path

    def send(self, path, expected_url, client_factory=CatalogClient):
        record = load_json(path, {})
        if record.get('target') != expected_url.rstrip('/'):
            raise ValueError('待传记录所属服务器与当前设置不一致')
        receipt = Path(path).with_name(Path(path).name.replace('.request.json', '.receipt.json'))
        if receipt.is_file():
            return load_json(receipt, {})
        client = client_factory(record['target'])
        status_path = Path(path).with_name(Path(path).name.replace('.request.json', '.status.json'))
        def status(state, detail=''):
            try:
                atomic_write_json(status_path, dict(state=state, detail=detail, time=datetime.now(timezone.utc).isoformat()))
            except Exception:
                pass  # Monitoring must never change delivery results.
        try:
            status('上传中 / 等待回执')
            capability = client.connect()
            if capability.get('version') != 1 or capability.get('authentication') != 'none':
                raise ValueError('服务器未提供免登录产品库 v1，请检查部署版本')
            response = client.add(record['product'])
            product_id = response.get('product_id', '')
            if len(product_id) != 32 or any(c not in '0123456789abcdef' for c in product_id):
                raise ValueError('服务端返回无效产品ID，保留原请求供原样重试')
            atomic_write_json(receipt, response)
            status('上传成功')
            return response
        except Exception:
            status('待补传 / 结果未确认', '连接、协议或字段校验失败；保留原请求重试，勿重新上架')
            raise
        finally:
            client.close()

    def retry(self, url, log):
        success = failed = 0
        for path in self.root.glob('*.request.json'):
            if load_json(path, {}).get('target') != url.rstrip('/'):
                continue
            if path.with_name(path.name.replace('.request.json', '.receipt.json')).is_file():
                continue
            try:
                self.send(path, url)
                success += 1
            except Exception:
                failed += 1
                log('共享产品资料仍待补传；原请求已保留，请检查连接或字段冲突')
        log(f'共享资料补传结束：成功 {success}，待处理 {failed}')


def upload_after_listing(root, row, images, log):
    """All failures, including local outbox writes, are nonfatal to platform listing."""
    path = None
    try:
        config = load_json(Path(root) / 'shared-catalog-settings.json', {})
        if not config.get('enabled'):
            return
        outbox = CatalogOutbox(root)
        path = outbox.prepare(row, config['base_url'], images)
        outbox.send(path, config['base_url'])
        log('共享产品资料已入库；图片文件仍独立保存在 OSS')
    except Exception:
        if path is None:
            try:
                atomic_write_json(Path(root) / 'shared-catalog-outbox' / (uuid.uuid4().hex + '.failure.json'),
                    dict(target='', sku=str(row.get('offer_id', '')), status='资料准备失败', product_id='',
                         time=datetime.now(timezone.utc).isoformat(), detail='请核对台账字段及服务配置后补传'))
            except Exception:
                pass
        log('共享产品资料上传未完成；上架与本地台账已保留，可从台账补传，不要重新上架')
