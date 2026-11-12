from pathlib import Path
p=Path('RFBS上品工具/pool_review_ui.py'); s=p.read_text(encoding='utf-8'); pos=s.index('    def _pool_rules(self):')
s=s[:pos]+'''    def _show_pool_source_comparison(self, item, parent=None):
        import base64
        import io
        import threading
        import webbrowser
        from urllib.parse import urlparse
        from PIL import Image, ImageTk
        window = tk.Toplevel(parent or self.root)
        window.title('Ozon / 1688 双图审核 — ' + str(item.get('sku', '')))
        window.geometry('900x650')
        window._photos = []
        panels = ttk.Frame(window)
        panels.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        def open_link(url, domain):
            host = (urlparse(url).hostname or '').lower()
            if urlparse(url).scheme in ('http', 'https') and (host == domain or host.endswith('.' + domain)):
                webbrowser.open(url)
            else:
                messagebox.showwarning('链接不可用', '表格未提供有效的商品链接', parent=window)
        for column, (title, data_key, url_key, link_key, domain) in enumerate((
            ('Ozon 商品', 'ozon_image_data', 'image_url', 'listing_url', 'ozon.ru'),
            ('1688 货源', 'supplier_image_data', 'supplier_image_url', 'supplier_1688_url', '1688.com'),
        )):
            frame = ttk.LabelFrame(panels, text=title)
            frame.grid(row=0, column=column, sticky='nsew', padx=5)
            panels.columnconfigure(column, weight=1)
            label = ttk.Label(frame, text='图片加载中…', anchor='center')
            label.pack(fill=tk.BOTH, expand=True)
            ttk.Button(frame, text='打开 ' + title + '链接', command=lambda key=link_key, d=domain: open_link(str(item.get(key) or ''), d)).pack(pady=8)
            def load(label=label, data=item.get(data_key), url=item.get(url_key)):
                try:
                    if data:
                        raw = base64.b64decode(data)
                    elif str(url).startswith(('https://', 'http://')):
                        import requests
                        response = requests.get(url, timeout=15)
                        response.raise_for_status()
                        raw = response.content
                    else:
                        raise ValueError('表格未提供图片')
                    picture = Image.open(io.BytesIO(raw))
                    picture.thumbnail((390, 370))
                    picture.load()
                    def show():
                        if window.winfo_exists():
                            photo = ImageTk.PhotoImage(picture, master=window)
                            window._photos.append(photo)
                            label.configure(image=photo, text='')
                    self._ui_call(show)
                except Exception:
                    def failed():
                        if window.winfo_exists():
                            label.configure(text='图片缺失或加载失败，请打开商品链接核对')
                    self._ui_call(failed)
            threading.Thread(target=load, daemon=True).start()
        panels.rowconfigure(0, weight=1)
        details = ('货源：' + str(item.get('supplier_title') or '待补') + '\\n'
                   + '拿货价：' + str(item.get('supplier_unit_price') or '待补')
                   + '  国内运费：' + str(item.get('domestic_freight') or '待补')
                   + '  到手成本 CNY：' + str(item.get('purchase_cost') or '待补')
                   + '  起批量：' + str(item.get('supplier_min_qty') or '待补') + '\\n'
                   + '匹配置信度：' + str(item.get('source_confidence') or '待确认')
                   + '\\n' + str(item.get('notes') or ''))
        ttk.Label(window, text=details, wraplength=850).pack(fill=tk.X, padx=16, pady=10)
        ttk.Label(window, text='请对照图片、规格及实际采购价，在商品编辑页确认货源与经济参数。').pack(pady=8)

'''+s[pos:]
s=s.replace('        def open_product():','''        def compare():
            if tree.selection():
                self._show_pool_source_comparison(items[int(tree.selection()[0])], window)
        ttk.Button(window, text='双图 / 货源审核', command=compare).pack(side=tk.LEFT, padx=8)
        def open_product():''',1); p.write_text(s,encoding='utf-8')
p=Path('RFBS上品工具/app.py'); s=p.read_text(encoding='utf-8'); s=s.replace('        variables = {}\n        commission_label = None', '''        ttk.Button(form, text='双图对照 / 打开1688货源', command=lambda: self._show_pool_source_comparison(item, window)).grid(row=2 + len(MAPPING_FIELDS), column=1, sticky='e')
        variables = {}
        commission_label = None''',1); p.write_text(s,encoding='utf-8')
