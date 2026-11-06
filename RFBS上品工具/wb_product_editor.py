"""Product modifications, independent of warehouse and stock rows."""
import re
import time
import tkinter as tk
from tkinter import ttk, messagebox
from core import atomic_write_json


class WbProductEditorMixin:
    def _build_wb_product_editor_tab(self):
        tab = ttk.Frame(self.wb_product_tabs, padding=8)
        self.wb_product_tabs.add(tab, text="7. 商品修改")
        self.wb_product_cards = {}
        self.wb_product_query = tk.StringVar()
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="货号 / WB ID / 条码（多个用逗号分隔）").pack(side=tk.LEFT)
        ttk.Entry(toolbar, textvariable=self.wb_product_query, width=45).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(toolbar, text="读取商品", command=lambda: self._run(self._wb_product_load)).pack(side=tk.LEFT)
        ttk.Label(tab, text="每个商品仅显示一行，与仓库和尺码数量无关。可多选商品批量修改图片。").pack(anchor='w', pady=8)
        self.wb_product_tree = ttk.Treeview(tab, columns=('vendor', 'nm', 'title', 'photos', 'status'), show='headings', selectmode='extended')
        for key, title in zip(('vendor', 'nm', 'title', 'photos', 'status'), ('货号', 'WB ID', '商品名称', '图片数量', '修改结果')):
            self.wb_product_tree.heading(key, text=title)
        self.wb_product_tree.pack(fill=tk.BOTH, expand=True)
        ttk.Button(tab, text="预览 / 修改图片（单个或批量）", command=self._wb_product_edit_images).pack(anchor='w', pady=8)

    def _wb_product_show(self, nm, status='已读取'):
        card = self.wb_product_cards.get(nm)
        if not card: return
        values = (card.get('vendorCode', ''), nm, card.get('title', ''), len(card.get('photos', [])), status)
        key = str(nm)
        if self.wb_product_tree.exists(key): self.wb_product_tree.item(key, values=values)
        else: self.wb_product_tree.insert('', tk.END, iid=key, values=values)

    def _wb_product_load(self):
        query = self._ui_call(self.wb_product_query.get).strip()
        if not query: raise ValueError('请输入商品货号、WB ID 或条码')
        api = self._wb_api_from_ui()
        cards = {}
        for term in filter(None, re.split(r'[,，;；\n]+', query)):
            for card in api.cards_by_text(term.strip()):
                cards[int(card['nmID'])] = card
        if not cards: raise ValueError('未找到商品')
        def apply():
            self.wb_product_cards = cards
            self.wb_product_tree.delete(*self.wb_product_tree.get_children())
            for nm in cards: self._wb_product_show(nm)
        self._ui_call(apply)
        self._wb_task_log(f'商品修改：已读取 {len(cards)} 个商品（按 WB ID 去重）')

    def _wb_product_edit_images(self):
        if getattr(self, 'busy', False): return
        products = {int(k): self.wb_product_cards[int(k)]['vendorCode'] for k in self.wb_product_tree.selection()}
        if not products:
            messagebox.showinfo('图片', '请先读取并选中一个或多个商品'); return
        self._run(lambda: self._wb_product_prepare_images(products))

    def _wb_product_prepare_images(self, products):
        api = self._wb_api_from_ui()
        cards = {}
        for nm, vendor in products.items():
            card = api.find_card(vendor)
            if not card or int(card['nmID']) != nm: raise ValueError('商品未找到，请重新读取')
            cards[nm] = card
        self._ui_call(lambda: self._wb_product_images_dialog(cards))

    def _wb_product_images_dialog(self, cards):
        from wb_image_editor import open_image_editor
        open_image_editor(self, cards)

    def _wb_product_submit_images(self, cards, changes):
        api = self._wb_api_from_ui()
        if api.sandbox: raise ValueError('请切换到正式环境')
        failures = []
        for nm, urls in changes.items():
            try:
                card = api.find_card(cards[nm]['vendorCode'])
                if not card or card.get('photos') != cards[nm].get('photos'): raise ValueError('图库已变化，请重新读取后修改')
                atomic_write_json(self.OUTPUT_DIR / f'wb-images-backup-{nm}-{time.time_ns()}.json', card)
                from wb_image_editor import resolve_sources
                sources = resolve_sources(urls, self._ui_call(lambda: self._config()['oss']), str(nm))
                if card.get('video'): sources.append(card['video'])
                result = api._request('POST', api.content_url + '/content/v3/media/save', json_body={'nmId': nm, 'data': sources})
                if isinstance(result, dict) and result.get('error'): raise RuntimeError(str(result))
                status = '图片修改已提交，等待同步后重新读取核对'
            except Exception as exc:
                status = '图片修改失败：' + str(exc); failures.append(str(nm) + ': ' + status)
            def apply(nm=nm, status=status):
                self._wb_product_show(nm, status)
            self._ui_call(apply)
            self._wb_task_log(str(nm) + ': ' + status)
        if failures: raise RuntimeError('；'.join(failures))