from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .client import MercadoLibreReadClient
from .models import AccountSnapshot, ListingRoute, Picture, ProductDraft, utc_now
from .storage import MercadoLibreStore
from .workflow import prepare_listing


ROUTES = {
    "新版 User Products（默认）": "user_products",
    "传统 Global Selling（待适配）": "global_selling",
    "本土卖家（待适配）": "local",
    "全托管（待适配）": "fully_managed",
    "尚未识别": "unknown",
}
PRICE_MODES = {"售价 price（USD）": "price", "净收入 global_net_proceeds（USD）": "global_net_proceeds"}


class MercadoLibrePanel(ttk.Frame):
    """Self-contained UI, state, background queue and persistence boundary."""

    def __init__(self, parent, data_dir: Path):
        super().__init__(parent, padding=10)
        self.store = MercadoLibreStore(data_dir)
        self.form: dict[str, tk.StringVar] = {}
        self.current = ProductDraft()
        self.pictures: list[Picture] = []
        self.targets: list[dict] = []
        self.account = AccountSnapshot()
        self.category_cache: dict[str, tuple[dict, list[dict]]] = {}
        self.events = queue.Queue()
        self.busy = False
        self._dead = False
        self._poll_id = None
        self._build()
        self._display(self.current)
        self._refresh_drafts()
        self.bind("<Destroy>", self._destroyed, add="+")
        self._poll_id = self.after(100, self._poll)

    def _var(self, key, default=""):
        if key not in self.form:
            self.form[key] = tk.StringVar(master=self, value=default)
        return self.form[key]

    def _entry(self, parent, label, key, row, column=0, **kwargs):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=4, pady=5)
        entry = ttk.Entry(parent, textvariable=self._var(key), **kwargs)
        entry.grid(row=row, column=column + 1, sticky="ew", padx=4, pady=5)
        return entry

    @staticmethod
    def _text(parent, height=8, readonly=False):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=4)
        text = tk.Text(frame, height=height, wrap="word", undo=not readonly)
        text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        if readonly:
            text.configure(state="disabled")
        return text

    @staticmethod
    def _replace(widget, text):
        old = str(widget.cget("state"))
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state=old)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        ttk.Label(self, text="美客多 · 独立上架工作区", font=("Microsoft YaHei UI", 13, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(self, text="第一阶段：商品家族 → 规格/站点 → 本地校验 → 请求预览。账号、草稿和图片独立保存。",
                  foreground="#586570").grid(row=1, column=0, sticky="w", pady=(4, 10))
        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew")
        left, right = ttk.Frame(panes, padding=4), ttk.Frame(panes, padding=4)
        panes.add(left, weight=1)
        panes.add(right, weight=4)
        ttk.Label(left, text="美客多商品草稿").pack(anchor="w")
        self.draft_tree = ttk.Treeview(left, columns=("sku", "name"), show="headings", selectmode="browse", height=12)
        self.draft_tree.heading("sku", text="货号")
        self.draft_tree.heading("name", text="产品家族")
        self.draft_tree.column("sku", width=95, minwidth=60)
        self.draft_tree.column("name", width=150, minwidth=80)
        self.draft_tree.pack(fill="both", expand=True, pady=6)
        self.draft_tree.bind("<Double-1>", lambda _event: self._guard(self._open_draft))
        ttk.Button(left, text="打开选中草稿", command=lambda: self._guard(self._open_draft)).pack(fill="x", pady=3)
        ttk.Button(left, text="新建商品", command=lambda: self._guard(self._new_draft)).pack(fill="x", pady=3)
        ttk.Label(left, text="切换商品时保存当前草稿。\n关闭软件前请点击保存。", foreground="#586570").pack(anchor="w", pady=8)
        toolbar = ttk.Frame(right)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="保存草稿", command=lambda: self._guard(self._save)).pack(side="left", padx=3)
        ttk.Button(toolbar, text="本地校验 / 生成预览", command=lambda: self._guard(self._prepare)).pack(side="left", padx=3)
        self.publish_button = ttk.Button(toolbar, text="真实上架（后续接入）", state="disabled")
        self.publish_button.pack(side="right", padx=3)
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)
        product = ttk.Frame(self.tabs, padding=8)
        sites = ttk.Frame(self.tabs, padding=8)
        media = ttk.Frame(self.tabs, padding=8)
        attributes = ttk.Frame(self.tabs, padding=8)
        account = ttk.Frame(self.tabs, padding=8)
        self.preview_tab = ttk.Frame(self.tabs, padding=8)
        for frame, title in ((product, "商品"), (sites, "站点"), (media, "图片"),
                             (attributes, "属性 / 多规格"), (account, "账号 / 类目"), (self.preview_tab, "校验 / 预览")):
            self.tabs.add(frame, text=title)
        self._build_product(product)
        self._build_sites(sites)
        self._build_media(media)
        self._build_attributes(attributes)
        self._build_account(account)
        ttk.Label(self.preview_tab, text="结果为本地候选请求；缺项会列出原因。尚未进行平台验证或真实发布。", wraplength=650).pack(anchor="w")
        self.preview = self._text(self.preview_tab, readonly=True)
        self.status = tk.StringVar(master=self, value="可离线准备商品；账号读取需要自己的美客多 Access Token")
        ttk.Label(self, textvariable=self.status, foreground="#245a8d", wraplength=980).grid(row=3, column=0, sticky="w", pady=(8, 0))

    def _build_product(self, tab):
        tab.columnconfigure(1, weight=1)
        tab.columnconfigure(3, weight=1)
        self._entry(tab, "本地商品名称", "title", 0)
        self._entry(tab, "卖家货号", "seller_sku", 0, 2)
        self._entry(tab, "英文家族名称", "family_name", 1)
        self._entry(tab, "CBT 类目 ID", "category_id", 1, 2)
        self._entry(tab, "金额（USD）", "price", 2)
        self._entry(tab, "库存（件）", "available_quantity", 2, 2)
        ttk.Label(tab, text="价格模式").grid(row=3, column=0, sticky="w", padx=4)
        ttk.Combobox(tab, textvariable=self._var("price_mode"), values=list(PRICE_MODES), state="readonly").grid(row=3, column=1, sticky="ew", padx=4)
        ttk.Label(tab, text="发布流程").grid(row=3, column=2, sticky="w", padx=4)
        ttk.Combobox(tab, textvariable=self._var("route"), values=list(ROUTES), state="readonly").grid(row=3, column=3, sticky="ew", padx=4)
        self._entry(tab, "绑定账号 ID", "account_id", 4)
        self._entry(tab, "采购链接", "supplier_url", 5)
        self._entry(tab, "采购货号", "supplier_sku", 5, 2)
        self._entry(tab, "采购成本（CNY）", "cost_cny", 6)
        self._entry(tab, "采购备注", "notes", 6, 2)
        ttk.Label(tab, text="英文描述（家族名称勿添加颜色/尺码；规格属性在多规格中填写）", wraplength=670).grid(row=7, column=0, columnspan=4, sticky="w", padx=4, pady=(8, 4))
        container = ttk.Frame(tab)
        container.grid(row=8, column=0, columnspan=4, sticky="nsew")
        tab.rowconfigure(8, weight=1)
        self.description = self._text(container, height=7)

    def _build_sites(self, tab):
        ttk.Label(tab, text="先准备目标站点，接入后按账号返回的站点/物流组合核对。所有金额使用 USD。", wraplength=650).pack(anchor="w")
        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=8)
        self.site_var = tk.StringVar(master=self, value="MLM")
        self.logistic_var = tk.StringVar(master=self, value="remote")
        self.listing_var = tk.StringVar(master=self, value="")
        ttk.Label(controls, text="站点").pack(side="left")
        self.site_combo = ttk.Combobox(controls, textvariable=self.site_var, values=("MLM", "MLB", "MLC", "MCO"), width=7)
        self.site_combo.pack(side="left", padx=4)
        ttk.Combobox(controls, textvariable=self.logistic_var, values=("remote", "fulfillment"), state="readonly", width=12).pack(side="left", padx=4)
        ttk.Label(controls, text="刊登类型（可选）").pack(side="left")
        ttk.Entry(controls, textvariable=self.listing_var, width=12).pack(side="left", padx=4)
        ttk.Button(controls, text="添加", command=lambda: self._guard(self._add_target)).pack(side="left", padx=4)
        self.target_tree = ttk.Treeview(tab, columns=("site", "logistic", "listing"), show="headings", height=10)
        for key, label in (("site", "站点"), ("logistic", "物流方式"), ("listing", "刊登类型")):
            self.target_tree.heading(key, text=label)
            self.target_tree.column(key, width=120)
        self.target_tree.pack(fill="both", expand=True, pady=6)
        ttk.Button(tab, text="移除选中站点", command=self._remove_target).pack(anchor="w")

    def _build_media(self, tab):
        ttk.Label(tab, text="图片按商品分别保存并编号；第一张为首图。新版发布需要先上传取得美客多图片 ID。", wraplength=650).pack(anchor="w")
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill="x", pady=8)
        for label, command in (("添加本地图片", self._add_images), ("添加来源 URL", self._add_image_url),
                               ("填写图片 ID", self._set_picture_id), ("设为首图", self._primary_image), ("移除引用", self._remove_image)):
            ttk.Button(toolbar, text=label, command=lambda fn=command: self._guard(fn)).pack(side="left", padx=3)
        self.image_tree = ttk.Treeview(tab, columns=("no", "file", "id"), show="headings", height=12)
        for key, label, width in (("no", "编号", 45), ("file", "本地路径 / 来源 URL", 330), ("id", "平台图片 ID", 160)):
            self.image_tree.heading(key, text=label)
            self.image_tree.column(key, width=width)
        self.image_tree.pack(fill="both", expand=True)

    def _build_attributes(self, tab):
        ttk.Label(tab, text="高级结构编辑：属性、保修条款和规格可先保存。包装尺寸、GTIN 等必填项由所选类目决定。", wraplength=650).pack(anchor="w")
        inner = ttk.Notebook(tab)
        inner.pack(fill="both", expand=True, pady=6)
        self.json_editors = {}
        for key, title, hint in (
            ("attributes", "公共属性", '格式：[{"id":"BRAND","value_name":"品牌"}]；SELLER_SKU 由货号自动生成。'),
            ("variations", "多规格", '留 [] 使用商品页单规格。每个规格填写 seller_sku、price、available_quantity、attributes、picture_ids。'),
            ("sale_terms", "保修条款", '格式同属性数组；使用当前类目允许的 WARRANTY_TYPE / WARRANTY_TIME 等字段。'),
        ):
            frame = ttk.Frame(inner, padding=6)
            inner.add(frame, text=title)
            ttk.Label(frame, text=hint, wraplength=650).pack(anchor="w")
            self.json_editors[key] = self._text(frame)

    def _build_account(self, tab):
        settings = self.store.load_settings()
        form = ttk.Frame(tab)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((("account_label", "账号备注"), ("app_id", "App ID（预留）"), ("redirect_uri", "OAuth 回调地址（预留）"))):
            self._entry(form, label, key, row)
            self._var(key).set(settings.get(key, ""))
        self.token = tk.StringVar(master=self)
        ttk.Label(form, text="Access Token").grid(row=3, column=0, sticky="w", padx=4)
        ttk.Entry(form, textvariable=self.token, show="*").grid(row=3, column=1, sticky="ew", padx=4, pady=5)
        ttk.Label(tab, text="Token 仅用于当前会话。此阶段可读取账号和类目；OAuth 授权交换与图片上传将在下一阶段接入。", wraplength=650).pack(anchor="w", pady=6)
        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=4)
        for label, command in (("保存账号备注", self._save_settings), ("读取账号 / 站点", self._read_account),
                               ("绑定当前草稿", self._bind_account), ("读取当前类目", self._read_category)):
            ttk.Button(controls, text=label, command=lambda fn=command: self._guard(fn)).pack(side="left", padx=3)
        self.account_output = self._text(tab, readonly=True)

    def _guard(self, fn):
        try:
            return fn()
        except (ValueError, OSError, RuntimeError, TypeError) as error:
            messagebox.showerror("美客多工作区", str(error), parent=self)

    def _collect(self):
        data = self.current.to_dict()
        for key in ("title", "family_name", "seller_sku", "category_id", "price", "available_quantity", "account_id"):
            data[key] = self._var(key).get().strip()
        data["route"] = ROUTES[self._var("route").get()]
        data["price_mode"] = PRICE_MODES[self._var("price_mode").get()]
        data["description"] = self.description.get("1.0", "end-1c").strip()
        data["procurement"] = {key: self._var(key).get().strip() for key in ("supplier_url", "supplier_sku", "cost_cny", "notes")}
        data["targets"] = self.targets
        data["pictures"] = [asdict(picture) for picture in self.pictures]
        for key, editor in self.json_editors.items():
            try:
                data[key] = json.loads(editor.get("1.0", "end-1c"))
            except json.JSONDecodeError as error:
                raise ValueError(f"{key} JSON 格式错误：第 {error.lineno} 行 {error.msg}") from None
        return ProductDraft.from_dict(data)

    def _display(self, draft):
        self.current = draft
        for key in ("title", "family_name", "seller_sku", "category_id", "price", "available_quantity", "account_id"):
            self._var(key).set(getattr(draft, key))
        self._var("route").set(next((label for label, value in ROUTES.items() if value == draft.route), "尚未识别"))
        self._var("price_mode").set(next((label for label, value in PRICE_MODES.items() if value == draft.price_mode), next(iter(PRICE_MODES))))
        for key, value in asdict(draft.procurement).items():
            self._var(key).set(value)
        self._replace(self.description, draft.description)
        data = draft.to_dict()
        self.targets = data["targets"]
        self.pictures = [Picture(**item) for item in data["pictures"]]
        for key, editor in self.json_editors.items():
            self._replace(editor, json.dumps(data[key], ensure_ascii=False, indent=2))
        self._refresh_images()
        self._refresh_targets()
        self._replace(self.preview, "点击“本地校验 / 生成预览”查看当前商品的缺项与候选请求。")

    def _refresh_drafts(self):
        self.draft_tree.delete(*self.draft_tree.get_children())
        for draft in self.store.list_drafts():
            self.draft_tree.insert("", "end", iid=draft.id, values=(draft.seller_sku or "多规格/未填", draft.family_name or draft.title or "未命名商品"))
        if self.store.warnings:
            self.status.set("；".join(self.store.warnings))

    def _save(self):
        draft = self._collect()
        draft.updated_at = utc_now()
        path = self.store.save_draft(draft)
        self.current = draft
        self._refresh_drafts()
        self.status.set(f"草稿已保存：{path}")
        return draft

    def _save_before_switch(self):
        draft = self._collect()
        if draft.to_dict() != self.current.to_dict():
            self._save()

    def _new_draft(self):
        self._save_before_switch()
        self._display(ProductDraft())
        self.tabs.select(0)
        self.status.set("已新建美客多商品，填写后保存草稿")

    def _open_draft(self):
        selection = self.draft_tree.selection()
        if selection:
            if selection[0] == self.current.id:
                self._save_before_switch()
                return
            draft = self.store.load_draft(selection[0])
            self._save_before_switch()
            self._display(draft)

    def _prepare(self):
        draft = self._save()
        category, attributes = self.category_cache.get(draft.category_id, (None, None))
        plan = prepare_listing(draft, account=self.account, category=category, attribute_definitions=attributes)
        path = self.store.save_plan(plan)
        report = "\n".join(f"{'需补充' if item.severity == 'error' else '待核实'} · {item.message}" for item in plan.issues)
        self._replace(self.preview, report + "\n\n" + json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        self.tabs.select(self.preview_tab)
        self.status.set(f"本地检查完成，{'请补充缺项' if plan.payload is None else '候选请求已生成（未发布）'}。记录：{path}")

    def _refresh_targets(self):
        self.target_tree.delete(*self.target_tree.get_children())
        for index, target in enumerate(self.targets):
            self.target_tree.insert("", "end", iid=str(index), values=(target["site_id"], target["logistic_type"], target.get("listing_type_id", "")))

    def _add_target(self):
        site, logistic = self.site_var.get().strip().upper(), self.logistic_var.get()
        if any((item["site_id"], item["logistic_type"]) == (site, logistic) for item in self.targets):
            raise ValueError("该站点与物流组合已经存在")
        self.targets.append({"site_id": site, "logistic_type": logistic, "listing_type_id": self.listing_var.get().strip(), "price": ""})
        self._refresh_targets()

    def _remove_target(self):
        selection = self.target_tree.selection()
        if selection:
            self.targets.pop(int(selection[0]))
            self._refresh_targets()

    def _refresh_images(self):
        self.image_tree.delete(*self.image_tree.get_children())
        for index, picture in enumerate(self.pictures):
            self.image_tree.insert("", "end", iid=str(index), values=(f"{index + 1:02}", picture.local_path or picture.source, picture.picture_id or "待上传"))

    def _add_images(self):
        paths = filedialog.askopenfilenames(parent=self, title="选择美客多产品图片", filetypes=[("商品图片", "*.jpg *.jpeg *.png")])
        if paths:
            self.pictures.extend(self.store.import_images(self.current.id, [Path(path) for path in paths]))
            self._refresh_images()

    def _add_image_url(self):
        value = simpledialog.askstring("来源图片", "图片公开 URL（仅记录来源）", parent=self)
        if value and value.strip():
            self.pictures.append(Picture(source=value.strip()))
            self._refresh_images()

    def _set_picture_id(self):
        selection = self.image_tree.selection()
        if not selection:
            raise ValueError("请先添加并选中一张图片")
        picture = self.pictures[int(selection[0])]
        value = simpledialog.askstring("美客多图片 ID", "填写该账号已上传图片返回的 ID", initialvalue=picture.picture_id, parent=self)
        if value is not None:
            picture.picture_id = value.strip()
            self._refresh_images()

    def _primary_image(self):
        selection = self.image_tree.selection()
        if selection:
            self.pictures.insert(0, self.pictures.pop(int(selection[0])))
            self._refresh_images()

    def _remove_image(self):
        selection = self.image_tree.selection()
        if selection:
            self.pictures.pop(int(selection[0]))
            self._refresh_images()

    def _save_settings(self):
        self.store.save_settings({key: self._var(key).get().strip() for key in ("app_id", "redirect_uri", "account_label")})
        self.status.set("美客多账号备注已保存，Token 仅保留在当前会话")

    def _background(self, action, on_done):
        if self.busy:
            raise ValueError("美客多查询正在进行，请等待返回")
        self.busy = True
        self.status.set("正在读取美客多 API…")
        def worker():
            try:
                result = action()
                self.events.put((on_done, result, None))
            except Exception as error:
                # The client exposes sanitized errors; never include a token in UI logs.
                from .client import MercadoLibreAPIError
                text = str(error) if isinstance(error, (MercadoLibreAPIError, ValueError)) else "读取失败，请检查网络与账号配置"
                self.events.put((None, None, text))
        threading.Thread(target=worker, daemon=True, name="mercadolibre-read").start()

    def _read_account(self):
        api = MercadoLibreReadClient(self.token.get().strip())
        def read():
            me = api.me()
            sites = api.marketplaces(str(me["id"])) if me.get("site_id") == "CBT" else {"marketplaces": []}
            return me, sites
        def done(result):
            me, sites = result
            self.account = AccountSnapshot.from_api(me, sites)
            self._replace(self.account_output, json.dumps({"account": asdict(self.account), "route": self.account.route.value}, ensure_ascii=False, indent=2))
            self.site_combo.configure(values=sorted({item["site_id"] for item in self.account.marketplaces if "site_id" in item}))
            self.status.set(f"已识别账号 {self.account.user_id}：{self.account.route.value}，可绑定当前草稿")
        self._background(read, done)

    def _bind_account(self):
        if not self.account.checked_at:
            raise ValueError("请先读取账号和站点")
        self._var("account_id").set(self.account.user_id)
        self._var("route").set(next(label for label, value in ROUTES.items() if value == self.account.route.value))
        self.status.set("当前草稿已绑定美客多账号，请在站点页选择该账号已开通的站点/物流")

    def _read_category(self):
        category_id = self._var("category_id").get().strip()
        api = MercadoLibreReadClient(self.token.get().strip())
        def done(result):
            self.category_cache[category_id] = result
            self._replace(self.account_output, json.dumps({"category": result[0], "attributes": result[1]}, ensure_ascii=False, indent=2))
            self.status.set(f"已读取 {category_id} 类目及属性，可重新执行本地校验")
        self._background(lambda: (api.category(category_id), api.category_attributes(category_id)), done)

    def _poll(self):
        if self._dead:
            return
        try:
            while True:
                callback, result, error = self.events.get_nowait()
                self.busy = False
                if error:
                    self.status.set(error)
                else:
                    self._guard(lambda: callback(result))
        except queue.Empty:
            pass
        self._poll_id = self.after(100, self._poll)

    def _destroyed(self, event):
        if event.widget is self:
            self._dead = True
            if self._poll_id is not None:
                self.after_cancel(self._poll_id)
