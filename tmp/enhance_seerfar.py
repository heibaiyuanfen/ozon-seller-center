from pathlib import Path
p=Path('RFBS上品工具/seerfar_import.py')
s=p.read_text(encoding='utf-8').replace('import re','import re\nimport base64\nfrom decimal import Decimal, InvalidOperation',1).replace('read_only=True, data_only=False','read_only=False, data_only=False',1)
s=s.replace('        products = []','''        embedded = {}
        for picture in sheet._images:
            anchor = getattr(picture.anchor, '_from', None)
            if anchor is not None:
                embedded[(anchor.row + 1, anchor.col)] = base64.b64encode(picture._data()).decode('ascii')

        def image_at(row_number, header):
            return embedded.get((row_number, index.get(header)), '')

        def link_at(row_number, header):
            position = index.get(header)
            if position is None:
                return ''
            c = sheet.cell(row_number, position + 1)
            return _text(c.hyperlink.target) if c.hyperlink else _text(c.value)

        products = []''',1)
s=s.replace('            products.append({','''            supplier_url = link_at(excel_row, '货源链接')
            if supplier_url.isdigit():
                supplier_url = 'https://detail.1688.com/offer/' + supplier_url + '.html'
            if not supplier_url.startswith(('https://', 'http://')):
                supplier_url = ''
            cost = _text(cell(row, '成本合计(¥)'))
            try:
                amount = Decimal(cost)
                cost = str(amount) if amount.is_finite() and amount >= 0 else ''
            except InvalidOperation:
                cost = ''
            products.append({
                'supplier_1688_url': supplier_url,
                'purchase_cost': cost,
                'supplier_title': _text(cell(row, '货源商品标题')),
                'supplier_unit_price': _text(cell(row, '拿货价(¥)')),
                'domestic_freight': _text(cell(row, '国内运费(¥)')),
                'supplier_min_qty': _text(cell(row, '起批量')),
                'source_confidence': _text(cell(row, '置信度')),
                'source_match_basis': _text(cell(row, '匹配依据')),
                'notes': _text(cell(row, '备注')),
                'ozon_image_data': image_at(excel_row, 'OZON高清主图'),
                'supplier_image_data': image_at(excel_row, '货源主图'),
                'supplier_image_url': _text(cell(row, '货源主图')),
''',1)
s=s.replace('item[field] = _text(saved.get(field)) if isinstance(saved, dict) else ""','item[field] = _text(saved[field]) if isinstance(saved, dict) and field in saved else _text(item.get(field))')
p.write_text(s,encoding='utf-8')
