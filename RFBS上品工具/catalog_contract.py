"""Shared catalog DATA_MODEL.md 1.2.0; API routes remain v1."""
import ipaddress
import math
import re
from urllib.parse import urlsplit


PRODUCT_FIELDS = {'source', 'external_id', 'sku', 'name', 'supplier', 'source_url',
                  'description_category_id', 'type_id', 'variants'}
VARIANT_FIELDS = {'sku', 'specification', 'color', 'size', 'purchase_price_cny',
                  'package_length_mm', 'package_width_mm', 'package_height_mm', 'weight_grams', 'images'}


def text_field(value, field, maximum, required=False):
    if not isinstance(value, str):
        raise ValueError(f'{field} 必须为字符串；未知文本请留空，不要填 null')
    value = value.strip()
    if len(value) > maximum or (required and not value):
        raise ValueError(f'{field} 长度须为 {1 if required else 0}–{maximum} 个字符')
    return value


def public_url(value):
    value = text_field(value, '公开链接', 4000)
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').rstrip('.').lower()
        if (parsed.scheme != 'https' or not host or parsed.port not in (None, 443)
                or parsed.username is not None or parsed.password is not None
                or '?' in value or '#' in value or any(char.isspace() for char in value)
                or '%' in host or host == 'localhost' or host.endswith('.localhost')):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.encode('idna').decode('ascii').split('.')
            if (len(labels) < 2 or len(host) > 253 or all(part.isdigit() for part in labels)
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in labels)):
                raise ValueError()
        else:
            if not address.is_global or address.is_reserved or address.is_multicast:
                raise ValueError()
    except (ValueError, UnicodeError):
        raise ValueError('链接须为公开 HTTPS，端口为默认或443，不含认证信息、查询串、片段或本机/私有/保留地址') from None
    return value


def valid_number(value, field, positive=False):
    if value is None:
        return
    try:
        finite = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < 0 or (positive and value == 0):
        raise ValueError(f'{field} 须为有限的{"正数" if positive else "非负数"}，未知值请留空或传 null')


def validate_product(product):
    if not isinstance(product, dict) or set(product) - PRODUCT_FIELDS:
        raise ValueError('产品对象含未定义字段；不得发送 source_type、status、售价、库存或平台发布请求')
    source = text_field(product.get('source'), 'source', 60, True)
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,59}', source):
        raise ValueError('source 来源程序名格式不正确')
    for field, maximum in (('external_id', 200), ('sku', 200), ('name', 600)):
        text_field(product.get(field), field, maximum, True)
    for field, maximum in (('supplier', 120), ('source_url', 4000)):
        if field in product:
            text_field(product[field], field, maximum)
    if product.get('source_url'):
        public_url(product['source_url'])
    for field in ('description_category_id', 'type_id'):
        value = product.get(field)
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError(f'{field} 须为正整数或 null')
    variants = product.get('variants')
    if not isinstance(variants, list) or not 1 <= len(variants) <= 100:
        raise ValueError('variants 须包含1–100个规格')
    skus = set()
    for variant in variants:
        if not isinstance(variant, dict) or set(variant) - VARIANT_FIELDS:
            raise ValueError('规格对象含未定义字段')
        sku = text_field(variant.get('sku'), '规格sku', 200, True)
        if sku in skus:
            raise ValueError('同一产品内的规格SKU不能重复')
        skus.add(sku)
        for field, maximum in (('specification', 500), ('color', 200), ('size', 200)):
            if field in variant:
                text_field(variant[field], field, maximum)
        for field in ('purchase_price_cny', 'package_length_mm', 'package_width_mm', 'package_height_mm', 'weight_grams'):
            valid_number(variant.get(field), field, positive=field != 'purchase_price_cny')
        images = variant.get('images', [])
        if not isinstance(images, list) or len(images) > 15:
            raise ValueError('每个规格最多15张图片，images必须为数组')
        urls = [public_url(value) for value in images]
        if any(not value for value in urls) or len(urls) != len(set(urls)):
            raise ValueError('图片链接不可为空或重复；无图请传空数组')


def validate_upload_capabilities(value):
    if (not isinstance(value, dict) or value.get('version') != 1 or value.get('authentication') != 'none'
            or value.get('upload_library') != 'wff' or value.get('initial_status') != 'ready'):
        raise ValueError('服务器尚未声明 wff/ready 免登录上传能力，请先更新服务器部署；未发送新增请求')


def validate_receipt(value):
    if not isinstance(value, dict):
        raise ValueError('服务端回执格式错误，保留原请求供原样重试')
    product_id = value.get('product_id')
    created, replayed = value.get('created'), value.get('replayed')
    if (not isinstance(product_id, str) or not re.fullmatch(r'[0-9a-f]{32}', product_id)
            or type(created) is not bool or type(replayed) is not bool or created == replayed):
        raise ValueError('服务端产品ID或created/replayed回执无效，保留原请求供原样重试')
