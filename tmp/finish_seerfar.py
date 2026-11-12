from pathlib import Path
p=Path('RFBS上品工具/app.py'); s=p.read_text(encoding='utf-8'); line="        ttk.Button(form, text='双图对照 / 打开1688货源', command=lambda: self._show_pool_source_comparison(item, window)).grid(row=2 + len(MAPPING_FIELDS), column=1, sticky='e')\n"; s=s.replace(line,''); s=s.replace('        ttk.Button(buttons, text="保存", command=save).pack(side=tk.LEFT, padx=5)', "        ttk.Button(buttons, text='双图对照 / 1688链接', command=lambda: self._show_pool_source_comparison(item, window)).pack(side=tk.LEFT, padx=5)\n        ttk.Button(buttons, text=\"保存\", command=save).pack(side=tk.LEFT, padx=5)",1); p.write_text(s,encoding='utf-8')
p=Path('RFBS上品工具/seerfar_import.py'); s=p.read_text(encoding='utf-8').replace("                'supplier_1688_url': supplier_url,", "                'enriched_source': '1' if '成本合计(¥)' in index else '',\n                'supplier_1688_url': supplier_url,"); s=s.replace('        if not item["price"]:', '''        if product.get('enriched_source') == '1':
            for field in ('supplier_1688_url', 'purchase_cost', 'notes'):
                item[field] = _text(product.get(field))
            item['supplier_confirmed'] = '0'
            item['economics_confirmed'] = '0'
            item['review_confirmed_at'] = ''
        if not item["price"]:'''); p.write_text(s,encoding='utf-8')
