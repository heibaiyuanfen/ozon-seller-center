"""ERP 1.1 ledger enrichment with shared, persisted product identities."""
import copy
import hashlib
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from core import atomic_write_json, load_json
from erp_backup import BackupOutbox, base_url, no_credentials, now, outbox_lock, product_snapshot


def text(value):
    return '' if value is None else str(value).strip()


def decimal_money(value):
    if value is None or text(value) == '':
        return None
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or number > 1000000000000:
        raise ValueError('金额必须是有效非负数')
    rounded = number.quantize(Decimal('.0001'), rounding=ROUND_HALF_UP)
    if abs(rounded - number) > Decimal('.00000001'):
        raise ValueError('金额最多四位小数')
    return format(rounded, 'f')


def positive(value, integer=False):
    if value is None or text(value) == '':
        return None
    number = Decimal(str(value))
    if not number.is_finite() or not 0 < number <= 1000000000:
        raise ValueError('尺寸、重量或类目 ID 必须为有效正数')
    if integer and number != int(number):
        raise ValueError('类目和类型 ID 必须为整数')
    return int(number) if number == int(number) else float(number)


def row_key(row):
    store = text(row.get('shop_config_id'))
    offer = text(row.get('offer_id'))
    if not store or not offer:
        return None
    return 'store:' + json.dumps([store, offer], ensure_ascii=False)


class IncrementalOutbox(BackupOutbox):
    def identity(self, row, inputs, target):
        with outbox_lock(self.root):
            path = self.root / 'identity.json'
            state = load_json(path, {'namespace': uuid.uuid4().hex, 'records': {}})
            bindings = state.setdefault('enrichment_bindings', {})
            aliases = []
            key = row_key(row)
            if key:
                aliases.append(key)
            group = text(inputs.get('product_group_id') or row.get('product_group_id'))
            if group:
                aliases.append('group:' + group)
            pid = text(inputs.get('backup_product_id') or row.get('backup_product_id'))
            vid = text(inputs.get('backup_variant_id') or row.get('backup_variant_id'))
            if pid and vid:
                aliases.append('explicit:' + json.dumps([pid, vid]))
            if not aliases:
                marker = row.get('_ledger_identity')
                if not marker:
                    raise ValueError('缺少稳定关联；请先为无货号行保存本地身份')
                aliases.append('row:' + marker)
            prefix = base_url(target) + '\n'
            known = [bindings[prefix + alias] for alias in aliases if prefix + alias in bindings]
            if len({(entry['product_id'], entry['variant_id']) for entry in known}) > 1:
                raise ValueError('历史产品身份冲突，需人工核对，不能自动合并两个已入库产品')
            identity = copy.deepcopy(known[0]) if known else dict(product_id=pid or uuid.uuid4().hex, variant_id=vid or uuid.uuid4().hex)
            if known and pid and vid and (identity['product_id'], identity['variant_id']) != (pid, vid):
                raise ValueError('已有店铺货号与明确产品/规格身份不一致，需人工核对')
            signature = text(inputs.get('supplier_sku_id') or inputs.get('base_offer_id') or row.get('base_offer_id'))
            if identity.get('signature') and signature and identity['signature'] != signature:
                raise ValueError('相同任务组出现不同可售规格，请人工核对')
            if signature:
                identity['signature'] = signature
            # Retain identities already used by the previous complete-backup client.
            job_id = inputs.get('_job_id')
            if job_id:
                legacy_key = hashlib.sha256((base_url(target) + '\n' + job_id).encode()).hexdigest()
                legacy = state.get('records', {}).get(legacy_key)
                if legacy:
                    if known and (identity['product_id'], identity['variant_id']) != (legacy['product_id'], legacy['variant_id']):
                        raise ValueError('旧备份已有独立产品，不能自动合并，请人工迁移')
                    identity.update(product_id=legacy['product_id'], variant_id=legacy['variant_id'])
            for alias in aliases:
                bindings[prefix + alias] = identity
            listing_key = prefix + (key or 'row:' + str(row.get('_ledger_identity')))
            identity['listing_id'] = state.setdefault('enrichment_listings', {}).setdefault(listing_key, uuid.uuid4().hex)
            identity['namespace'] = state['namespace']
            atomic_write_json(path, state)
            return identity

    def prepare_row(self, row, inputs, target, submitted=None, definitions=()):
        identity = self.identity(row, inputs, target)
        product, assets = ledger_product(row, identity, submitted, definitions)
        document = dict(format='erp.follow-backup', schema_version='1.1.0', producer={'namespace': identity['namespace']},
                        assets=assets, products=[product])
        no_credentials(document)
        digest = hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with outbox_lock(self.root):
            # Immutable content-addressed manifests: retry never changes export_id or bytes.
            index_path = self.root / 'enrichment-exports.json'
            index = load_json(index_path, {})
            key = base_url(target) + '\n' + digest
            if key in index:
                return self.root / (index[key] + '.json')
            document.update(export_id=uuid.uuid4().hex, exported_at=now())
            path = self.root / (document['export_id'] + '.json')
            atomic_write_json(path, document)
            atomic_write_json(path.with_suffix('.target.json'), {'base_url': base_url(target)})
            index[key] = document['export_id']
            atomic_write_json(index_path, index)
            return path


def ledger_product(row, identity, submitted=None, definitions=()):
    vid = identity['variant_id']
    variant = dict(external_variant_id=vid, sku=row.get('offer_id'),
        content={'title': row.get('title')}, supplier={'url': row.get('supplier_1688_url') or None},
        cost={'currency': 'CNY', 'purchase': decimal_money(row.get('purchase_cost'))},
        package={}, item_dimensions={}, category=dict(platform='ozon',
            description_category_id=positive(row.get('category_id'), True), type_id=positive(row.get('type_id'), True),
            path=[part.strip() for part in text(row.get('category_display')).split('/') if part.strip()]))
    # Old ledger headers alone do not establish the units used by the submitted payload.
    dimensions_unit = text(row.get('dimension_unit'))
    weight_unit = text(row.get('weight_unit'))
    raw_item = (submitted or {}).get('item', {})
    if raw_item:
        dimensions_unit = text(raw_item.get('dimension_unit'))
        weight_unit = text(raw_item.get('weight_unit'))
    dim_factor = {'mm': 1, 'cm': 10, 'm': 1000}.get(dimensions_unit)
    weight_factor = {'g': 1, 'kg': 1000}.get(weight_unit)
    for ledger_key, item_key, target_key in (('depth_mm', 'depth', 'length_mm'), ('width_mm', 'width', 'width_mm'), ('height_mm', 'height', 'height_mm')):
        raw = raw_item.get(item_key) if raw_item else row.get(ledger_key)
        if dim_factor is not None and raw is not None and text(raw):
            # New ledgers have already been normalized to mm/g.
            factor = dim_factor if raw_item else 1
            variant['package'][target_key] = positive(Decimal(str(raw)) * factor)
    raw_weight = raw_item.get('weight') if raw_item else row.get('gross_weight_g')
    if weight_factor is not None and raw_weight is not None and text(raw_weight):
        variant['package']['weight_g'] = positive(Decimal(str(raw_weight)) * (weight_factor if raw_item else 1))
    variant['package'].update(dimensions_source='unknown', weight_source='unknown')
    if row.get('net_weight_g') is not None and weight_unit == 'g':
        variant['item_dimensions'] = {'weight_g': positive(row['net_weight_g']), 'weight_source': 'unknown'}
    listing = dict(external_listing_id=identity['listing_id'], external_variant_id=vid, platform='ozon',
        source_store={'external_store_id': text(row.get('shop_config_id')), 'name': row.get('shop_name')},
        offer_id=row.get('offer_id'), product_id=text(row.get('ozon_product_id')) or None,
        status='unknown', pricing=dict(currency=row.get('currency_code') or None,
            price=decimal_money(row.get('price')), old_price=decimal_money(row.get('old_price'))),
        extensions={'ledger_row': {k: v for k, v in row.items() if not k.startswith('_')}})
    assets = []
    if submitted:
        success = next((entry for entry in submitted.get('import_result', {}).get('result', {}).get('items', [])
                        if str(entry.get('offer_id')) == str(row.get('offer_id')) and entry.get('status') == 'imported' and not entry.get('errors')), None)
        if success:
            listing.update(status='imported', product_id=text(success.get('product_id')) or listing['product_id'])
        if success and listing.get('product_id'):
            full_identity = {**identity, 'published_at': now()}
            full_product, assets = product_snapshot(submitted, row, definitions, full_identity)
            full_variant = full_product['variants'][0]
            for key in ('content', 'attributes', 'complex_attributes', 'images', 'attributes_complete'):
                variant[key] = full_variant[key]
            listing['submitted_payload'] = full_product['listings'][0]['submitted_payload']
    product = dict(external_product_id=identity['product_id'], name=row.get('title'), sku=row.get('offer_id'),
                   source_url=row.get('ozon_source_url') or None, variants=[variant])
    # A missing store identity is not replaced by a guessed store name.
    if listing['source_store']['external_store_id']:
        product['listings'] = [listing]
    else:
        product['extensions'] = {'ledger_row': {k: v for k, v in row.items() if not k.startswith('_')}}
    return product, assets
