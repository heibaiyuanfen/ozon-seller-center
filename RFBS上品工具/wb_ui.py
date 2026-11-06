from __future__ import annotations

import copy
import json
import re
import shutil
import threading
import time
import uuid
import tkinter as tk
from decimal import Decimal, InvalidOperation
from pathlib import Path
from PIL import Image, ImageOps, ImageTk
from tkinter import filedialog, messagebox, ttk

from core import atomic_write_json, load_json
from product_ledger import upsert_product_ledger
from services import WbCardAiService
from wb import (
    WildberriesApi, WbListingWorkflow, build_card_content_document,
    build_card_payload, parse_card_content_document, validate_image_paths,
)
from wb_pricing import WbPricingBreakdown, calculate_wb_price
from wb_source import (
    download_wb_source_images,
    normalize_wb_source,
    scrape_wb_source,
)


from wb_inventory import WbInventoryMixin
from wb_product_editor import WbProductEditorMixin


class WbUiMixin(WbInventoryMixin, WbProductEditorMixin):
    """Wildberries UI and resumable listing workflow for ``RfbsListingApp``."""

    def _initialize_wb_state(self):
        self.wb_parents: list[dict] = []
        self.wb_parent_by_display: dict[str, dict] = {}
        self.wb_subjects: list[dict] = []
        self.wb_subject_by_display: dict[str, dict] = {}
        self.wb_characteristics: list[dict] = []
        self.wb_characteristic_values: dict[str, object] = {}
        self.wb_directory_by_display: dict[str, object] = {}
        self.wb_image_paths: list[str] = []
        self.wb_warehouse_by_display: dict[str, dict] = {}
        self.wb_source_reference: dict = {}
        self.wb_job: dict = {}
        self.wb_pricing_breakdown: WbPricingBreakdown | None = None
        self._wb_pricing_trace_suspended = False

    def _build_wb(self):
        tab = self.wb_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        connection = ttk.LabelFrame(tab, text="WB API", padding=8)
        connection.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        connection.columnconfigure(1, weight=1)
        ttk.Label(connection, text="个人 Token").grid(row=0, column=0, sticky="w", padx=4)
        ttk.Entry(
            connection, textvariable=self._var("wb_token"), show="*",
        ).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Checkbutton(
            connection, text="Sandbox", variable=self._var("wb_sandbox", "0"),
            onvalue="1", offvalue="0", command=self._refresh_wb_config_state,
        ).grid(row=0, column=2, padx=5)
        ttk.Button(
            connection, text="保存", command=self._save_wb_config,
        ).grid(row=0, column=3, padx=4)
        ttk.Button(
            connection, text="测试权限", command=lambda: self._run(self._test_wb_connection),
        ).grid(row=0, column=4, padx=4)
        self.wb_connection_var = tk.StringVar(value="尚未配置 WB API Token")
        ttk.Label(
            connection, textvariable=self.wb_connection_var, foreground="#586570",
        ).grid(row=1, column=0, columnspan=5, sticky="w", padx=4, pady=(6, 0))

        self.wb_product_tabs = ttk.Notebook(tab)
        self.wb_product_tabs.grid(row=1, column=0, sticky="nsew")
        product_tab = ttk.Frame(self.wb_product_tabs, padding=10)
        category_tab = ttk.Frame(self.wb_product_tabs, padding=10)
        media_tab = ttk.Frame(self.wb_product_tabs, padding=10)
        self.wb_product_tabs.add(product_tab, text="1. 商品信息")
        self.wb_product_tabs.add(category_tab, text="2. 类目与属性")
        self.wb_product_tabs.add(media_tab, text="3. 图片与库存")
        self._build_wb_product_tab(product_tab)
        self._build_wb_category_tab(category_tab)
        self._build_wb_media_tab(media_tab)
        self._build_wb_variants_tab()
        self._wb_on_listing_mode_changed()
        self._wb_on_fulfillment_mode_changed()

        status = ttk.LabelFrame(tab, text="WB 上架任务", padding=8)
        status.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        status.columnconfigure(1, weight=1)
        self.wb_submit_button = ttk.Button(
            status, text="开始上架", command=self._confirm_wb_submit, state="disabled",
        )
        self.wb_submit_button.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 10))
        self.wb_progress = ttk.Progressbar(status, maximum=6, mode="determinate")
        self.wb_progress.grid(row=0, column=1, sticky="ew")
        self.wb_status_var = tk.StringVar(value="API 尚未配置")
        ttk.Label(status, textvariable=self.wb_status_var, foreground="#245a8d").grid(
            row=1, column=1, sticky="w", pady=(5, 0),
        )
        self.wb_detail_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.wb_detail_var, foreground="#8a5a00").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0),
        )
        ttk.Button(status, text="重置当前任务", command=self._reset_wb_job).grid(
            row=0, column=2, rowspan=2, padx=(10, 0), sticky="ns",
        )

        log_frame = ttk.Frame(status)
        log_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.wb_task_log_text = tk.Text(log_frame, height=4, wrap="word", state="disabled")
        self.wb_task_log_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        log_scroll = ttk.Scrollbar(log_frame, command=self.wb_task_log_text.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.wb_task_log_text.configure(yscrollcommand=log_scroll.set)
        details = ttk.Frame(self.wb_product_tabs, padding=8)
        self.wb_product_tabs.add(details, text="5. 任务流程")
        ttk.Label(details, text="读取资料 → AI 文案与属性 → 创建商品卡 → 上传图片 → 设置价格 → 写入库存 → 合并确认").pack(anchor="w")
        self.wb_task_tree = ttk.Treeview(details, columns=("vendor", "stage", "status", "message"), show="headings")
        for key, title, width in (("vendor", "货号", 160), ("stage", "当前阶段", 140), ("status", "状态", 100), ("message", "运行信息 / 错误", 700)):
            self.wb_task_tree.heading(key, text=title)
            self.wb_task_tree.column(key, width=width)
        self.wb_task_tree.pack(fill=tk.BOTH, expand=True)
        ttk.Button(details, text="编辑选中任务资料 / 修正后重试", command=lambda: self._wb_edit_failed_task(True)).pack(anchor="w")

        self._build_wb_inventory_tab()
        self._build_wb_product_editor_tab()

    def _wb_task_log(self, message):
        self._log(message)
        if not hasattr(self, "wb_task_log_text"):
            return
        line = f"{time.strftime('%H:%M:%S')}  {message}\n"
        def update():
            self.wb_task_log_text.configure(state="normal")
            self.wb_task_log_text.insert(tk.END, line)
            self.wb_task_log_text.see(tk.END)
            self.wb_task_log_text.configure(state="disabled")
            self.wb_status_var.set(str(message))
            if "失败 / 暂停" in str(message) and hasattr(self, "wb_task_tree"):
                for iid in self.wb_task_tree.get_children():
                    values = list(self.wb_task_tree.item(iid, "values"))
                    if values[2] not in ("completed", "failed"):
                        values[2:] = ["failed", str(message)]
                        self.wb_task_tree.item(iid, values=values)
        self._ui_call(update)

    def _wb_task_state(self, state, batch=None):
        if not hasattr(self, "wb_task_tree"):
            return
        stage = state.get("stage", "new")
        labels = {"new": "准备资料", "card_submitted": "等待商品卡", "card_ready": "上传图片",
                  "media_uploaded": "等待价格同步", "price_submitted": "等待价格生效",
                  "price_ready": "写入库存", "completed": "上架完成", "preparing": "AI 生成资料"}
        vendor = str(state.get("inputs", {}).get("vendor_code") or "当前商品")
        message = state.get("error") or state.get("message") or labels.get(stage, stage)
        rows = batch or [state]
        maximum = len(rows) * 6 + (1 if batch else 0)
        progress = sum(WbListingWorkflow.STAGES.get(item.get("stage", "new"), 0) for item in rows)
        if batch and all(item.get("group_verified") for item in rows):
            progress += 1
        def update():
            values = (vendor, labels.get(stage, stage), state.get("status", "running"), message)
            if self.wb_task_tree.exists(vendor):
                self.wb_task_tree.item(vendor, values=values)
            else:
                self.wb_task_tree.insert("", tk.END, iid=vendor, values=values)
            self.wb_progress.configure(maximum=maximum, value=progress)
            self.wb_detail_var.set(f"{vendor}：{message}")
        self._ui_call(update)

    def _build_wb_variants_tab(self):
        tab = ttk.Frame(self.wb_product_tabs, padding=10)
        self.wb_product_tabs.add(tab, text="4. 多变体")
        ttk.Label(tab, text="选择变体可预览图片；双击编辑。独立图库模式使用各行货号；同卡尺码模式使用商品信息页货号。",
                  wraplength=1000).pack(anchor="w")
        self.wb_variant_mode = tk.StringVar(value="group")
        modes = ttk.Frame(tab)
        modes.pack(fill=tk.X)
        ttk.Radiobutton(modes, text="独立图片、价格，合并商品组", variable=self.wb_variant_mode, value="group").pack(side=tk.LEFT)
        ttk.Radiobutton(modes, text="同卡尺码，共用图库和售价", variable=self.wb_variant_mode, value="sizes").pack(side=tk.LEFT)
        warehouse = ttk.Frame(tab)
        warehouse.pack(fill=tk.X, pady=6)
        ttk.Button(warehouse, text="读取仓库", command=lambda: self._run(self._wb_load_warehouses)).pack(side=tk.LEFT)
        self.wb_variant_warehouse_combo = ttk.Combobox(warehouse, textvariable=self._var("wb_warehouse_display"), state="readonly", width=55)
        self.wb_variant_warehouse_combo.pack(side=tk.LEFT, padx=8)
        self.wb_variant_warehouse_combo.bind("<<ComboboxSelected>>", self._wb_warehouse_selected)
        ttk.Label(warehouse, text="各行库存写入此仓库；双击变体修改库存和图片").pack(side=tk.LEFT)
        saved_settings = load_json(self.OUTPUT_DIR / "wb-variant-settings.json", {})
        if isinstance(saved_settings, dict):
            for key in ("wb_pricing_mode", "wb_manual_currency", "wb_price", "wb_warehouse_display", "wb_warehouse_id"):
                if key in saved_settings:
                    self._var(key).set(str(saved_settings[key]))
            if saved_settings.get("wb_pricing_mode") == "manual":
                self._var("wb_manual_price").set(saved_settings.get("wb_price", ""))
        self._wb_variants_lock = threading.Lock()
        self.wb_variant_rows = load_json(self.OUTPUT_DIR / "wb-variant-draft.json", [])
        if not isinstance(self.wb_variant_rows, list):
            self.wb_variant_rows = []
        self.wb_variant_tree = ttk.Treeview(tab, columns=("vendor", "style", "size", "price", "stock", "images"), show="headings")
        for key, label in zip(self.wb_variant_tree["columns"], ("货号", "颜色/款式", "尺码", "售价", "库存", "图片数")):
            self.wb_variant_tree.heading(key, text=label)
        self.wb_variant_tree.pack(fill=tk.BOTH, expand=True)
        self.wb_variant_tree.bind("<<TreeviewSelect>>", self._wb_preview_variant)
        self.wb_variant_tree.bind("<Double-1>", lambda _e: self._wb_edit_variant())
        self.wb_variant_preview = ttk.Frame(tab, height=150)
        self.wb_variant_preview.pack(fill=tk.X)
        for row in self.wb_variant_rows:
            self._wb_show_variant_row(row)
        ttk.Button(tab, text="编辑选中变体 / 图片", command=self._wb_edit_variant).pack(side=tk.LEFT)
        ttk.Button(tab, text="编辑上架资料 / 修正错误", command=self._wb_edit_failed_task).pack(side=tk.LEFT)
        ttk.Button(tab, text="保存变体草稿", command=self._wb_save_variant_draft).pack(side=tk.LEFT)
        ttk.Button(tab, text="添加变体", command=self._wb_add_variant).pack(side=tk.LEFT)
        ttk.Button(tab, text="删除选中变体", command=self._wb_delete_variants).pack(side=tk.LEFT)
        ttk.Button(tab, text="批量上架 / 继续", command=lambda: self._run(self._wb_run_variants)).pack(side=tk.LEFT)

    def _wb_save_variant_draft(self):
        atomic_write_json(self.OUTPUT_DIR / "wb-variant-draft.json", self.wb_variant_rows)
        keys = ("wb_pricing_mode", "wb_manual_currency", "wb_price", "wb_warehouse_display", "wb_warehouse_id")
        atomic_write_json(self.OUTPUT_DIR / "wb-variant-settings.json",
                          {key: self.vars[key].get() for key in keys if key in self.vars})
        if hasattr(self, "wb_status_var"):
            self.wb_status_var.set(f"已保存 {len(self.wb_variant_rows)} 个变体草稿")

    def _wb_show_variant_row(self, row):
        values = (row["vendor_code"], row["style"], row["tech_size"], row["price"], row["stock"], len(row["image_paths"]))
        if self.wb_variant_tree.exists(row["id"]):
            self.wb_variant_tree.item(row["id"], values=values)
        else:
            self.wb_variant_tree.insert("", tk.END, iid=row["id"], values=values)

    @staticmethod
    def _wb_correct_task(state, changes):
        if state.get("stage") != "new":
            raise ValueError("此商品已提交或已创建，请先继续查询结果；不能重置为新商品重复提交")
        updated = copy.deepcopy(state)
        inputs = updated["inputs"]
        for key in ("title", "description", "brand", "characteristic_values"):
            if key in changes:
                inputs[key] = copy.deepcopy(changes[key])
        build_card_payload(dict(inputs, barcode=inputs.get("barcode") or "PREVIEW"))
        updated["previous_error"] = state.get("error") or state.get("previous_error", "")
        updated.update(status="queued", error="", message="已人工修正，等待重新提交", manually_corrected=True)
        updated.pop("payload", None)
        return updated

    def _wb_edit_failed_task(self, from_tasks=False):
        if getattr(self, "busy", False):
            messagebox.showwarning("任务运行中", "请等待当前任务结束后再修改提交资料")
            return
        mode = self.wb_variant_mode.get()
        path = self.OUTPUT_DIR / ("wb-group-job.json" if mode == "group" else "wb-sizes-job.json")
        batch = load_json(path, [])
        if not batch:
            messagebox.showinfo("尚无提交资料", "请先启动一次多变体任务，生成的文案和属性将保存在任务中")
            return
        tree = self.wb_task_tree if from_tasks else self.wb_variant_tree
        selected = tree.selection()
        selected_ids = set(selected)
        vendor = selected[0] if from_tasks and selected else None
        state = next((item for item in batch if (item.get("task_id") in selected_ids or item["inputs"]["vendor_code"] == vendor)), None)
        if mode == "sizes" and selected and not state:
            state = batch[0]
        if not selected:
            state = next((item for item in batch if item.get("stage") == "new" and item.get("status") == "failed"), None)
        if not state:
            messagebox.showinfo("选择任务", "请选择需要修改的变体，或在任务流程中选择对应货号")
            return
        if state.get("stage") != "new":
            messagebox.showinfo("不能重新创建", "该商品正在同步或已创建，请点击继续任务查询结果。仅被拒绝或尚未提交的商品可编辑后重新提交。")
            return
        original = copy.deepcopy(state)
        inputs = state["inputs"]
        dialog = tk.Toplevel(self.root)
        dialog.title("修正上架资料：" + inputs["vendor_code"])
        dialog.geometry("1000x740")
        ttk.Label(dialog, text="WB 返回：" + str(state.get("error") or state.get("previous_error") or "尚未提交"),
                  wraplength=950, foreground="#b03030").pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(dialog, text="这里修改实际提交的文案和属性。货号、条码和其他变体进度保留；重试不会重新运行 AI。").pack(anchor="w", padx=10)
        title = tk.StringVar(value=inputs.get("title", ""))
        brand = tk.StringVar(value=inputs.get("brand", ""))
        ttk.Label(dialog, text="标题").pack(anchor="w", padx=10)
        ttk.Entry(dialog, textvariable=title).pack(fill=tk.X, padx=10)
        ttk.Label(dialog, text="品牌（可选）").pack(anchor="w", padx=10)
        ttk.Entry(dialog, textvariable=brand).pack(fill=tk.X, padx=10)
        ttk.Label(dialog, text="描述").pack(anchor="w", padx=10)
        description = tk.Text(dialog, height=5, wrap="word")
        description.pack(fill=tk.X, padx=10)
        description.insert("1.0", inputs.get("description", ""))
        values = copy.deepcopy(inputs.get("characteristic_values") or {})
        definitions = {str(d["charcID"]):d for d in inputs.get("characteristic_definitions", [])}
        table = ttk.Treeview(dialog, columns=("name", "value"), show="headings", height=8)
        table.heading("name", text="属性名称（选择后编辑）")
        table.heading("value", text="当前提交值")
        table.column("name", width=260);table.column("value", width=650)
        table.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        for key, definition in definitions.items():
            table.insert("", tk.END, iid=key, values=(definition.get("name", key), json.dumps(values.get(key, ""), ensure_ascii=False)))
        edit = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=edit)
        entry.pack(fill=tk.X, padx=10)
        active = [None]
        def commit():
            key = active[0]
            if key is None:
                return
            raw = edit.get().strip()
            try:
                value = json.loads(raw)
            except ValueError:
                value = raw
            if raw:
                values[key] = value
            else:
                values.pop(key, None)
            table.set(key, "value", json.dumps(values.get(key, ""), ensure_ascii=False))
        def select(_event=None):
            commit()
            selection = table.selection()
            active[0] = selection[0] if selection else None
            options.set(""); options.configure(values=[]); options_map.clear()
            edit.set(json.dumps(values.get(active[0], ""), ensure_ascii=False) if active[0] else "")
        table.bind("<<TreeviewSelect>>", select)
        options = ttk.Combobox(dialog, state="readonly")
        options.pack(fill=tk.X, padx=10, pady=4)
        options_map = {}
        def load_options():
            key = active[0]
            if key is None:
                return
            try:
                kind = self._wb_directory_kind(definitions[key].get("name", ""))
                api = self._wb_api_from_ui()
            except ValueError as error:
                messagebox.showinfo("官方选项", str(error), parent=dialog)
                return
            def work():
                rows = api.directory_values(kind, subject_id=int(inputs["subject_id"]), locale="ru")
                def apply():
                    if not dialog.winfo_exists() or active[0] != key:
                        return
                    options_map.clear()
                    for raw in rows:
                        value = next((raw.get(k) for k in ("name", "value", "title", "id") if raw.get(k) not in (None, "")), raw) if isinstance(raw, dict) else raw
                        options_map[str(value)] = value
                    options.configure(values=list(options_map))
                    options.set("")
                self._ui_call(apply)
            self._run(work)
        def use_option():
            if options.get() in options_map:
                edit.set(json.dumps([options_map[options.get()]], ensure_ascii=False))
                commit()
        controls = ttk.Frame(dialog);controls.pack(fill=tk.X, padx=10, pady=6)
        ttk.Button(controls, text="应用属性修改", command=commit).pack(side=tk.LEFT)
        ttk.Button(controls, text="读取官方选项", command=load_options).pack(side=tk.LEFT)
        ttk.Button(controls, text="采用官方选项", command=use_option).pack(side=tk.LEFT)
        def save(retry=False):
            try:
                if getattr(self, "busy", False):
                    raise ValueError("请等待正在运行的操作结束")
                commit()
                latest = load_json(path, [])
                index = next((i for i, item in enumerate(latest) if item.get("task_id") == original.get("task_id")), None)
                if index is None or latest[index] != original:
                    raise ValueError("任务已发生变化，请关闭窗口后重新打开编辑")
                latest[index] = self._wb_correct_task(original, dict(title=title.get().strip(), brand=brand.get().strip(),
                    description=description.get("1.0", tk.END).strip(), characteristic_values=values))
                atomic_write_json(path.with_name(path.stem + ".before-edit-" + time.strftime("%Y%m%d-%H%M%S") + ".json"), load_json(path, []))
                atomic_write_json(path, latest)
                self._wb_task_state(latest[index], latest)
                self.vars["wb_vendor_code"].set(original.get("batch_vendor") or inputs["vendor_code"])
                self._wb_task_log(inputs["vendor_code"] + "：人工修改已保存，重试使用修正后的属性")
                dialog.destroy()
                if retry:
                    self._run(self._wb_run_variants)
            except Exception as error:
                messagebox.showerror("无法保存修改", str(error), parent=dialog)
        ttk.Button(controls, text="保存修正", command=save).pack(side=tk.RIGHT)
        ttk.Button(controls, text="保存并继续上架", command=lambda: save(True)).pack(side=tk.RIGHT)
        color = next((key for key, definition in definitions.items() if any(word in definition.get("name", "").lower() for word in ("颜色", "color", "цвет"))), None)
        if color:
            table.selection_set(color);table.see(color)

    def _wb_edit_variant(self):
        selection = self.wb_variant_tree.selection()
        if selection:
            self._wb_add_variant(next(row for row in self.wb_variant_rows if row["id"] == selection[0]))

    def _wb_preview_variant(self, _event=None):
        for child in self.wb_variant_preview.winfo_children():
            child.destroy()
        selection = self.wb_variant_tree.selection()
        if not selection:
            return
        row = next(row for row in self.wb_variant_rows if row["id"] == selection[0])
        self._wb_image_gallery(self.wb_variant_preview, row["image_paths"])

    def _wb_image_gallery(self, parent, paths):
        canvas = tk.Canvas(parent, height=150)
        canvas.pack(fill=tk.X)
        scroll = ttk.Scrollbar(parent, orient="horizontal", command=canvas.xview)
        scroll.pack(fill=tk.X)
        canvas.configure(xscrollcommand=scroll.set)
        canvas.photos = []
        for i, path in enumerate(paths):
            x = i * 145
            try:
                with Image.open(path) as source:
                    thumb = ImageOps.exif_transpose(source).convert("RGB")
                    thumb.thumbnail((135, 115))
                photo = ImageTk.PhotoImage(thumb, master=parent)
                canvas.photos.append(photo)
                canvas.create_image(x + 70, 60, image=photo)
            except (OSError, ValueError):
                canvas.create_text(x + 70, 60, text="图片无法读取")
            canvas.create_text(x + 70, 130, text=f"{i + 1}. {Path(path).name[:16]}")
        canvas.configure(scrollregion=(0, 0, max(1, len(paths)) * 145, 145))

    def _wb_delete_variants(self):
        for iid in self.wb_variant_tree.selection():
            self.wb_variant_rows = [row for row in self.wb_variant_rows if row["id"] != iid]
            self.wb_variant_tree.delete(iid)
        self._wb_save_variant_draft()
        self._wb_preview_variant()

    def _wb_add_variant(self, existing=None):
        dialog = tk.Toplevel(self.root)
        dialog.title("添加 WB 变体")
        fields = {}
        for index, (key, label) in enumerate((("vendor_code", "独立卖家货号"), ("style", "颜色/款式"),
                ("tech_size", "技术尺码"), ("wb_size", "WB 尺码"), ("price", "售价（店铺币种整数）"), ("stock", "库存"), ("purchase_cost", "采购成本 CNY（自动核价；留空用公共值）"),
                ("weight_kg", "重量 kg（自动核价；留空用公共值）"))):
            if key == "purchase_cost" and self.vars["wb_pricing_mode"].get() == "manual":
                fields[key] = tk.StringVar(value=(existing or {}).get(key, ""))
                continue
            ttk.Label(dialog, text=label).grid(row=index, column=0)
            fields[key] = tk.StringVar(value=(existing or {}).get(key, ""))
            ttk.Entry(dialog, textvariable=fields[key]).grid(row=index, column=1)
        paths = list((existing or {}).get("image_paths", []))
        gallery = ttk.Frame(dialog)
        gallery.grid(row=9, column=0, columnspan=2, sticky="ew")
        image_list = tk.Listbox(dialog, width=85, height=5, exportselection=False)
        image_list.grid(row=10, column=0, columnspan=2, sticky="ew")
        def refresh():
            image_list.delete(0, tk.END)
            for path in paths:
                image_list.insert(tk.END, path)
            for child in gallery.winfo_children():
                child.destroy()
            self._wb_image_gallery(gallery, paths)
        def select():
            added = filedialog.askopenfilenames(parent=dialog, title="添加该变体图片", filetypes=[("图片", "*.jpg *.jpeg *.png *.webp *.bmp")])
            paths.extend(path for path in added if path not in paths)
            refresh()
        def modify(action):
            selection = image_list.curselection()
            if not selection:
                return
            index = selection[0]
            if action == "remove":
                paths.pop(index)
            elif action == "replace":
                path = filedialog.askopenfilename(parent=dialog, title="替换此图片")
                if path:
                    paths[index] = path
            else:
                target = index + action
                if 0 <= target < len(paths):
                    paths[index], paths[target] = paths[target], paths[index]
            refresh()
        controls = ttk.Frame(dialog)
        controls.grid(row=8, column=0, columnspan=2)
        ttk.Button(controls, text="添加图片", command=select).pack(side=tk.LEFT)
        for label, action in (("替换", "replace"), ("删除", "remove"), ("上移", -1), ("下移", 1)):
            ttk.Button(controls, text=label, command=lambda action=action: modify(action)).pack(side=tk.LEFT)
        refresh()
        def save():
            try:
                row = {key: value.get().strip() for key, value in fields.items()}
                if not row["vendor_code"] or any(r["vendor_code"] == row["vendor_code"] and r["id"] != (existing or {}).get("id") for r in self.wb_variant_rows):
                    raise ValueError("货号不能为空或重复")
                manual = self.vars["wb_pricing_mode"].get() == "manual"
                if (manual and int(row["price"]) <= 0) or int(row["stock"]) < 0:
                    raise ValueError("售价必须为正整数，库存不能为负数")
                validate_image_paths(paths, check_resolution=False)
                row.update(id=(existing or {}).get("id") or uuid.uuid4().hex, image_paths=list(paths))
                if existing:
                    existing.update(row)
                else:
                    self.wb_variant_rows.append(row)
                self._wb_show_variant_row(row)
                self._wb_save_variant_draft()
                self.wb_variant_tree.selection_set(row["id"])
                self._wb_preview_variant()
                dialog.destroy()
            except (ValueError, OSError) as error:
                messagebox.showerror("变体资料", str(error), parent=dialog)
        ttk.Button(dialog, text="保存", command=save).grid(row=11, column=1)

    def _wb_run_variants(self):
        lock = getattr(self, "_wb_variants_lock", None)
        if lock is None:
            lock = self._wb_variants_lock = threading.Lock()
        if not lock.acquire(blocking=False):
            raise ValueError("多变体任务正在运行，请勿重复启动")
        self._wb_task_log("开始多变体任务：校验资料和读取断点")
        try:
            result = self._wb_run_variants_locked()
            self._wb_task_log("多变体任务完成")
            return result
        except Exception as error:
            self._wb_task_log(f"多变体任务失败 / 暂停：{error}")
            raise
        finally:
            lock.release()

    def _wb_run_variants_locked(self):
        settings = self._ui_call(self._wb_connection_settings)
        base = self._ui_call(self._wb_collect_form_snapshot)
        if base.get("pricing_mode") == "overseas_warehouse":
            raise ValueError("海外仓运费公式待配置，请暂用直接设置售价模式")
        rows = self._ui_call(lambda: copy.deepcopy(self.wb_variant_rows))
        mode = self._ui_call(self.wb_variant_mode.get)
        path = self.OUTPUT_DIR / ("wb-group-job.json" if mode == "group" else "wb-sizes-job.json")
        if path.exists():
            batch = json.loads(path.read_text(encoding="utf-8"))
            if batch and all(x.get("stage") == "completed" and (mode != "group" or x.get("group_verified")) for x in batch):
                raise ValueError("此批次已完成，请勿重复上架")
            if batch and batch[0].get("batch_vendor", base["vendor_code"]) != base["vendor_code"]:
                raise ValueError("有另一商品的未完成任务，请先恢复该商品继续")
        else:
            batch = []
        if not settings["sandbox"] and base["warehouse_display"] not in self.wb_warehouse_by_display:
            raise ValueError("请读取仓库并选择本次上架的目标仓库")
        if batch:
            for state in batch:
                if state["stage"] != "completed":
                    state["inputs"]["warehouse_id"] = base["warehouse_id"]
                    state["inputs"]["warehouse_display"] = base["warehouse_display"]
        api = WildberriesApi(settings["token"], sandbox=settings["sandbox"])
        def save(_state):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
            if _state:
                self._wb_task_state(_state, batch)
        for state in batch:
            self._wb_task_state(state, batch)
        if not batch:
            if not rows:
                raise ValueError("请先添加变体")
            self._wb_task_log("读取 WB 类目属性")
            definitions = api.subject_characteristics(int(base["subject_id"]))
            config = self._ui_call(self._analysis_config)
            if mode == "sizes" and base.get("pricing_mode") == "manual" and len({row["price"] for row in rows}) != 1:
                raise ValueError("同卡尺码目前使用统一售价，请将各行售价设为相同")
            if mode == "sizes" and (len({row["tech_size"] for row in rows}) != len(rows) or any(not row["tech_size"] for row in rows)):
                raise ValueError("尺码不能为空或重复")
            if mode == "sizes" and base.get("pricing_mode") != "manual" and len({
                    (row.get("purchase_cost") or base.get("purchase_cost"), row.get("weight_kg") or base.get("weight_kg")) for row in rows}) > 1:
                raise ValueError("同卡尺码自动核价需使用相同成本和重量；不同参数请选择独立图片模式")
            size_rows = [{key: row[key] for key in ("tech_size", "wb_size", "stock")} for row in rows]
            shared = dict(rows[0], vendor_code=base["vendor_code"])
            if mode == "sizes" and any(row["image_paths"] != rows[0]["image_paths"] for row in rows):
                raise ValueError("同卡尺码共用图库；不同图片请选择独立图片合并商品组模式")
            if len(rows) > 30:
                raise ValueError("单个商品组最多 30 个变体")
            for index, row in enumerate((rows if mode == "group" else [shared]), 1):
                self._wb_task_log(f"准备第 {index}/{len(rows)} 个变体 {row['vendor_code']}：AI 生成标题、描述及属性")
                self._wb_task_state(dict(inputs=row, stage="preparing", status="running", message="正在生成标题、描述及属性"))
                inputs = dict(base, **{k: v for k, v in row.items() if k not in ("id", "image_paths")})
                for key in ("purchase_cost", "weight_kg"):
                    inputs[key] = row.get(key) or base.get(key)
                self._wb_apply_listing_price(inputs)
                inputs["characteristic_definitions"] = definitions
                generated = WbCardAiService(**config).generate(
                    subject_id=int(base["subject_id"]), subject_name=base["subject_display"],
                    definitions=definitions, image_paths=row["image_paths"],
                    current_document=build_card_content_document(inputs),
                    source_context={"style": row["style"], "reference": base.get("source_reference")})
                parsed = parse_card_content_document(generated, definitions, expected_subject_id=int(base["subject_id"]))
                for key in ("title", "description", "characteristic_values"):
                    inputs[key] = parsed[key]
                color = self._ui_call(lambda: self.vars["wb_uniform_color"].get() if "wb_uniform_color" in getattr(self, "vars", {}) else "")
                if color:
                    for definition in definitions:
                        if any(word in definition.get("name", "").lower() for word in ("颜色", "цвет", "color")):
                            inputs["characteristic_values"][str(definition["charcID"])] = [color]
                self._wb_validate_listing_inputs(inputs, sandbox=settings["sandbox"])
                # Manual mode preserves each row price; automatic mode keeps calculated price.
                if mode == "sizes":
                    inputs["size_variants"] = size_rows
                batch.append(dict(inputs=inputs, stage="new", task_id=row["id"], batch_vendor=base["vendor_code"],
                    image_paths=self._wb_copy_stable_images(row["image_paths"], row["vendor_code"])))
            save(None)
        workflow = WbListingWorkflow(api, save_state=save, log=self._wb_task_log)
        while any(state["stage"] != "completed" for state in batch):
            for state in batch:
                if state["stage"] == "completed":
                    continue
                self._wb_task_log(f"处理 {state['inputs']['vendor_code']}：{state['stage']}")
                try:
                    workflow.advance(state)
                except Exception as error:
                    state.update(status="failed", error=str(error))
                    save(state)
                    raise
                if state["stage"] == "completed":
                    upsert_product_ledger(self.PRODUCT_LEDGER_PATH, self._wb_product_ledger_record(state))
                    save(state)
            if any(state["stage"] != "completed" for state in batch):
                self._wb_task_log("等待 WB 异步同步，60 秒后自动查询；无需重复点击")
                time.sleep(60)
        if mode == "group" and len(batch) == 1:
            batch[0]["group_verified"] = True
            save(None)
        if mode == "group" and len(batch) > 1:
            self._wb_task_log("全部变体上架完成，正在查询并确认商品组合并")
            cards = [api.find_card(state["inputs"]["vendor_code"]) for state in batch]
            if any(not card or not card.get("imtID") for card in cards):
                raise RuntimeError("变体已上架，商品组信息仍在同步，请稍后继续")
            target = int(cards[0]["imtID"])
            if any(int(card["imtID"]) != target for card in cards):
                api.merge_cards(target, [state["nm_id"] for state in batch[1:]])
                cards = [api.find_card(state["inputs"]["vendor_code"]) for state in batch]
                if any(not card or int(card.get("imtID") or 0) != target for card in cards):
                    raise RuntimeError("合并请求已提交，尚未确认，请稍后点击继续")
            for state in batch:
                state["group_verified"] = True
            save(None)
        for state in batch:
            state["group_verified"] = True
            self._wb_task_state(state, batch)
        save(None)
        self._ui_call(lambda: messagebox.showinfo("WB 多变体", "全部变体已完成上架及合并"))

    def _wb_form_entry(self, parent, label, key, row, column=0, *, default="", width=34):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=5, pady=4)
        entry = ttk.Entry(parent, textvariable=self._var(key, default), width=width)
        entry.grid(row=row, column=column + 1, sticky="ew", padx=5, pady=4)
        return entry

    def _build_wb_product_tab(self, tab):
        tab.columnconfigure(1, weight=1)
        tab.columnconfigure(3, weight=1)

        mode_frame = ttk.LabelFrame(tab, text="WB 上品模式", padding=7)
        mode_frame.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        mode_frame.columnconfigure(3, weight=1)
        ttk.Label(mode_frame, text="模式").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        self.wb_listing_mode_buttons = []
        for column, text, value in (
            (1, "跟卖模式", "follow"),
            (2, "本地新品模式", "local"),
        ):
            button = ttk.Radiobutton(
                mode_frame,
                text=text,
                value=value,
                variable=self._var("wb_listing_mode", "local"),
                command=self._wb_on_listing_mode_changed,
            )
            button.grid(row=0, column=column, sticky="w", padx=6, pady=3)
            self.wb_listing_mode_buttons.append(button)
        ttk.Label(mode_frame, text="WB 来源链接/商品 ID").grid(
            row=1, column=0, sticky="w", padx=4, pady=3,
        )
        self.wb_source_entry = ttk.Entry(
            mode_frame, textvariable=self._var("wb_source_url"),
        )
        self.wb_source_entry.grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=4, pady=3,
        )
        self.wb_source_parse_button = ttk.Button(
            mode_frame,
            text="解析来源商品",
            command=lambda: self._run(self._wb_parse_follow_source),
        )
        self.wb_source_parse_button.grid(row=1, column=4, sticky="e", padx=4, pady=3)
        self._var("wb_source_nm_id")
        self.wb_source_status_var = tk.StringVar(value="本地新品模式：使用自己准备的成品图片")
        ttk.Label(
            mode_frame, textvariable=self.wb_source_status_var, foreground="#245a8d",
        ).grid(row=2, column=0, columnspan=5, sticky="w", padx=4, pady=(3, 0))

        self._wb_form_entry(
            tab, "卖家货号", "wb_vendor_code", 1,
            default=f"WB-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}",
        )
        self._wb_form_entry(tab, "品牌（可选）", "wb_brand", 1, 2)
        self._wb_form_entry(tab, "商品标题", "wb_title", 2, width=58)
        self._wb_form_entry(tab, "1688 采购链接（可选）", "wb_supplier_1688_url", 2, 2)
        ttk.Label(tab, text="商品描述").grid(row=3, column=0, sticky="nw", padx=5, pady=4)
        self.wb_description_text = tk.Text(tab, height=7, wrap="word")
        self.wb_description_text.grid(row=3, column=1, columnspan=3, sticky="nsew", padx=5, pady=4)
        tab.rowconfigure(3, weight=1)
        size_frame = ttk.LabelFrame(tab, text="规格（WB 使用厘米和千克）", padding=7)
        size_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 4))
        for column in (1, 3, 5, 7):
            size_frame.columnconfigure(column, weight=1)
        for column, label, key, default in (
            (0, "包装长(cm)", "wb_length_cm", "10"),
            (2, "包装宽(cm)", "wb_width_cm", "10"),
            (4, "包装高(cm)", "wb_height_cm", "10"),
            (6, "包装毛重(kg)", "wb_weight_kg", "1"),
        ):
            ttk.Label(size_frame, text=label).grid(row=0, column=column, sticky="w", padx=4, pady=3)
            ttk.Entry(size_frame, textvariable=self._var(key, default), width=13).grid(
                row=0, column=column + 1, sticky="ew", padx=4, pady=3,
            )
        for column, label, key in (
            (0, "技术尺码（卖家实际）", "wb_tech_size"),
            (2, "WB 尺码（平台展示）", "wb_size"),
            (4, "商品条码", "wb_barcode"),
        ):
            ttk.Label(size_frame, text=label).grid(row=1, column=column, sticky="w", padx=4, pady=3)
            ttk.Entry(size_frame, textvariable=self._var(key), width=18).grid(
                row=1, column=column + 1, sticky="ew", padx=4, pady=3,
            )
        ttk.Label(
            size_frame,
            text="无尺码商品两项留空；技术尺码示例 S/42，WB 尺码仅在类目要求时填写",
            foreground="#586570",
        ).grid(row=1, column=6, columnspan=2, sticky="e", padx=4)

        pricing = ttk.LabelFrame(tab, text="WB 利润率核价", padding=7)
        pricing.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        self._build_wb_pricing_panel(pricing)

    def _build_wb_pricing_panel(self, pricing):
        for column in (1, 3, 5, 7):
            pricing.columnconfigure(column, weight=1)
        ttk.Label(pricing, text="定价模式").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        for column, text, value in (
            (1, "跨境自动核价", "cross_border"),
            (3, "海外仓自动核价（待公式）", "overseas_warehouse"),
            (5, "直接设置售价", "manual"),
        ):
            ttk.Radiobutton(
                pricing, text=text, value=value,
                variable=self._var("wb_pricing_mode", "cross_border"),
                command=self._wb_on_pricing_mode_changed,
            ).grid(row=0, column=column, sticky="w", padx=4, pady=3)

        self._var("wb_fulfillment_mode", "cross_border")
        self._var("wb_manual_price", "")
        ttk.Label(pricing, text="手动售价币种").grid(row=5, column=0, sticky="w")
        ttk.Combobox(pricing, textvariable=self._var("wb_manual_currency", "CNY"),
                     values=("CNY", "RUB"), state="readonly", width=10).grid(row=5, column=1, sticky="w")
        ttk.Label(pricing, text="手动模式使用店铺实际结算币种，不自动换算；多变体使用各行售价。跨境自动核价输出 CNY。",
                  wraplength=850).grid(row=5, column=2, columnspan=6, sticky="w")
        self.vars["wb_manual_currency"].trace_add("write", self._wb_on_pricing_mode_changed)

        self.wb_auto_pricing_widgets = []
        def pricing_entry(label, key, row, column, default=""):
            label_widget = ttk.Label(pricing, text=label)
            label_widget.grid(
                row=row, column=column, sticky="w", padx=4, pady=3,
            )
            entry = ttk.Entry(
                pricing, textvariable=self._var(key, default), width=12,
            )
            entry.grid(row=row, column=column + 1, sticky="ew", padx=4, pady=3)
            if key != "wb_discount":
                self.wb_auto_pricing_widgets.extend((label_widget, entry))
            return label_widget, entry

        pricing_entry("采购成本(CNY)", "wb_purchase_cost", 1, 0)
        pricing_entry("贴标费(CNY)", "wb_label_fee", 1, 2, "0")
        pricing_entry("目标利润率(%)", "wb_target_profit_margin_percent", 1, 4, "20")
        pricing_entry("人工佣金(%)", "wb_sales_commission_percent", 1, 6, "25")
        pricing_entry("广告费率(%)", "wb_advertising_percent", 2, 0, "0")
        pricing_entry("货损率(%)", "wb_cargo_loss_percent", 2, 2, "0")
        pricing_entry(
            "汇率（1 CNY 兑 RUB）", "wb_cny_rub_rate", 2, 4, "12",
        )
        pricing_entry("折扣(%)", "wb_discount", 2, 6, "0")

        self.wb_cross_border_pricing = ttk.Frame(pricing)
        self.wb_cross_border_pricing.grid(row=3, column=0, columnspan=8, sticky="ew")
        self.wb_cross_border_fee_var = tk.StringVar(value="标准跨境运费：待计算")
        ttk.Label(
            self.wb_cross_border_pricing,
            textvariable=self.wb_cross_border_fee_var,
            foreground="#586570",
        ).grid(row=0, column=0, sticky="w", padx=4, pady=3)

        self.wb_overseas_pricing = ttk.Frame(pricing)
        for column in (1, 3, 5, 7):
            self.wb_overseas_pricing.columnconfigure(column, weight=1)

        def overseas_entry(label, key, column, default=""):
            ttk.Label(self.wb_overseas_pricing, text=label).grid(
                row=0, column=column, sticky="w", padx=4, pady=3,
            )
            ttk.Entry(
                self.wb_overseas_pricing,
                textvariable=self._var(key, default), width=12,
            ).grid(row=0, column=column + 1, sticky="ew", padx=4, pady=3)

        overseas_entry("头程运费(CNY/件)", "wb_first_mile_shipping", 0, "0")
        overseas_entry("海外仓操作费(CNY/件)", "wb_warehouse_operation_fee", 2, "0")
        overseas_entry("仓库物流系数(%)", "wb_logistics_coefficient_percent", 4, "100")
        overseas_entry("本地化指数", "wb_localization_index", 6, "1")
        self.wb_overseas_fee_var = tk.StringVar(value="海外仓物流：待计算")
        ttk.Label(
            self.wb_overseas_pricing,
            textvariable=self.wb_overseas_fee_var,
            foreground="#586570",
            wraplength=1050,
        ).grid(row=1, column=0, columnspan=8, sticky="w", padx=4, pady=3)

        self.wb_price_currency_var = tk.StringVar(value="自动标价(CNY)")
        ttk.Label(pricing, textvariable=self.wb_price_currency_var).grid(
            row=4, column=0, sticky="w", padx=4, pady=3,
        )
        self.wb_price_entry = ttk.Entry(
            pricing, textvariable=self._var("wb_price"), width=14, state="readonly",
        )
        self.wb_price_entry.grid(row=4, column=1, sticky="ew", padx=4, pady=3)
        self.wb_pricing_status_var = tk.StringVar(value="请填写完整核价参数")
        ttk.Label(
            pricing, textvariable=self.wb_pricing_status_var, foreground="#245a8d",
            wraplength=900,
        ).grid(row=4, column=2, columnspan=6, sticky="w", padx=4, pady=3)

        for key in (
            "wb_purchase_cost", "wb_label_fee", "wb_target_profit_margin_percent",
            "wb_sales_commission_percent", "wb_advertising_percent",
            "wb_cargo_loss_percent", "wb_cny_rub_rate", "wb_discount",
            "wb_first_mile_shipping", "wb_warehouse_operation_fee",
            "wb_logistics_coefficient_percent", "wb_localization_index",
            "wb_weight_kg", "wb_length_cm", "wb_width_cm", "wb_height_cm",
        ):
            self.vars[key].trace_add("write", self._wb_update_price)

    def _build_wb_category_tab(self, tab):
        tab.columnconfigure(1, weight=1)
        tab.columnconfigure(3, weight=1)
        tab.rowconfigure(3, weight=3)
        tab.rowconfigure(5, weight=2)
        ttk.Label(tab, text="父类目").grid(row=0, column=0, sticky="w", padx=5, pady=4)
        self.wb_parent_combo = ttk.Combobox(
            tab, textvariable=self._var("wb_parent_display"), state="normal",
        )
        self.wb_parent_combo.bind("<Return>", lambda _event: self._run(self._wb_load_parents))
        self.wb_parent_combo.grid(row=0, column=1, sticky="ew", padx=5, pady=4)
        self.wb_parent_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._run(self._wb_load_subjects),
        )
        ttk.Button(
            tab, text="搜索/加载父类目", command=lambda: self._run(self._wb_load_parents),
        ).grid(row=0, column=2, sticky="w", padx=5)
        ttk.Label(tab, text="子类目").grid(row=1, column=0, sticky="w", padx=5, pady=4)
        self.wb_subject_combo = ttk.Combobox(
            tab, textvariable=self._var("wb_subject_display"), state="normal",
        )
        self.wb_subject_combo.grid(row=1, column=1, sticky="ew", padx=5, pady=4)
        self.wb_subject_combo.bind("<<ComboboxSelected>>", lambda _event: self._run(self._wb_load_characteristics))
        self.wb_subject_combo.bind("<Return>", lambda _event: self._run(self._wb_search_subjects))
        ttk.Button(
            tab, text="搜索/刷新", command=lambda: self._run(self._wb_search_subjects),
        ).grid(row=1, column=2, sticky="w", padx=5)
        ttk.Button(
            tab, text="读取类目属性", command=lambda: self._run(self._wb_load_characteristics),
        ).grid(row=1, column=3, sticky="w", padx=5)
        self._var("wb_parent_id")
        self._var("wb_subject_id")

        ttk.Label(
            tab, text="红色值表示必填；双击或选中属性后在下方编辑。多个值可填写 JSON 数组。",
            foreground="#8a5a00",
        ).grid(row=2, column=0, columnspan=4, sticky="w", padx=5, pady=(4, 6))
        self.wb_attribute_tree = ttk.Treeview(
            tab, columns=("name", "required", "type", "unit", "value"),
            show="headings", selectmode="browse", height=9,
        )
        for column, title, width in (
            ("name", "属性", 230), ("required", "必填", 60),
            ("type", "类型", 90), ("unit", "单位", 75), ("value", "当前值", 420),
        ):
            self.wb_attribute_tree.heading(column, text=title)
            self.wb_attribute_tree.column(column, width=width, stretch=column in {"name", "value"})
        self.wb_attribute_tree.tag_configure("required", foreground="#b42318")
        self.wb_attribute_tree.grid(row=3, column=0, columnspan=4, sticky="nsew", padx=5)
        attr_scroll = ttk.Scrollbar(tab, orient="vertical", command=self.wb_attribute_tree.yview)
        attr_scroll.grid(row=3, column=4, sticky="ns")
        self.wb_attribute_tree.configure(yscrollcommand=attr_scroll.set)
        self.wb_attribute_tree.bind("<<TreeviewSelect>>", self._wb_attribute_selected)
        self.wb_attribute_tree.bind("<Double-1>", self._wb_attribute_selected)

        editor = ttk.LabelFrame(tab, text="编辑选中属性", padding=7)
        editor.grid(row=4, column=0, columnspan=4, sticky="ew", padx=5, pady=(8, 0))
        editor.columnconfigure(1, weight=1)
        self.wb_selected_attribute_var = tk.StringVar(value="请先选择属性")
        ttk.Label(editor, textvariable=self.wb_selected_attribute_var).grid(
            row=0, column=0, sticky="w", padx=4,
        )
        ttk.Entry(editor, textvariable=self._var("wb_attribute_value")).grid(
            row=0, column=1, sticky="ew", padx=4,
        )
        ttk.Button(editor, text="保存属性", command=self._wb_save_characteristic).grid(row=0, column=2, padx=4)
        ttk.Button(editor, text="清空", command=self._wb_clear_characteristic).grid(row=0, column=3, padx=4)
        self.wb_directory_combo = ttk.Combobox(
            editor, textvariable=self._var("wb_directory_display"), state="readonly",
        )
        self.wb_directory_combo.grid(row=1, column=1, sticky="ew", padx=4, pady=(6, 0))
        ttk.Button(
            editor, text="加载官方选项", command=lambda: self._run(self._wb_load_directory_options),
        ).grid(row=1, column=2, padx=4, pady=(6, 0))
        ttk.Button(editor, text="采用选项", command=self._wb_use_directory_value).grid(
            row=1, column=3, padx=4, pady=(6, 0),
        )

        json_frame = ttk.LabelFrame(
            tab, text="WB 内容 JSON（包装尺寸和重量仍在商品信息中独立填写）", padding=7,
        )
        json_frame.grid(row=5, column=0, columnspan=4, sticky="nsew", padx=5, pady=(8, 0))
        json_frame.columnconfigure(0, weight=1)
        json_frame.rowconfigure(1, weight=1)
        json_toolbar = ttk.Frame(json_frame)
        json_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(
            json_toolbar, text="同步当前值到 JSON", command=self._wb_sync_content_json,
        ).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(
            json_toolbar, text="应用 JSON", command=self._wb_apply_content_json,
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            json_toolbar, text="读取类目并用 AI 填写",
            command=lambda: self._run(self._wb_read_and_fill_content),
        ).pack(side=tk.LEFT, padx=5)
        self.wb_content_json_status_var = tk.StringVar(
            value="先读取类目属性，再由 AI 按 WB API 返回的字段填写；所有结果均可人工复核",
        )
        ttk.Label(
            json_toolbar, textvariable=self.wb_content_json_status_var, foreground="#245a8d",
        ).pack(side=tk.LEFT, padx=10)
        self.wb_content_json_text = tk.Text(json_frame, height=7, wrap="none")
        self.wb_content_json_text.grid(row=1, column=0, sticky="nsew")
        json_scroll = ttk.Scrollbar(
            json_frame, orient="vertical", command=self.wb_content_json_text.yview,
        )
        json_scroll.grid(row=1, column=1, sticky="ns")
        self.wb_content_json_text.configure(yscrollcommand=json_scroll.set)

    def _build_wb_media_tab(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(tab)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.wb_image_add_button = ttk.Button(
            toolbar, text="添加图片", command=self._wb_select_images,
        )
        self.wb_image_add_button.pack(side=tk.LEFT, padx=(0, 5))
        self.wb_image_remove_button = ttk.Button(
            toolbar, text="移除", command=self._wb_remove_image,
        )
        self.wb_image_remove_button.pack(side=tk.LEFT, padx=5)
        self.wb_image_up_button = ttk.Button(
            toolbar, text="上移", command=lambda: self._wb_move_image(-1),
        )
        self.wb_image_up_button.pack(side=tk.LEFT, padx=5)
        self.wb_image_down_button = ttk.Button(
            toolbar, text="下移", command=lambda: self._wb_move_image(1),
        )
        self.wb_image_down_button.pack(side=tk.LEFT, padx=5)
        self.wb_image_status_var = tk.StringVar(value="尚未选择图片；第 1 张作为主图")
        ttk.Label(toolbar, textvariable=self.wb_image_status_var, foreground="#245a8d").pack(
            side=tk.LEFT, padx=10,
        )
        self.wb_image_list = tk.Listbox(tab, height=13, selectmode=tk.BROWSE)
        self.wb_image_list.grid(row=1, column=0, sticky="nsew")
        media_scroll = ttk.Scrollbar(tab, orient="vertical", command=self.wb_image_list.yview)
        media_scroll.grid(row=1, column=1, sticky="ns")
        self.wb_image_list.configure(yscrollcommand=media_scroll.set)

        inventory = ttk.LabelFrame(tab, text="WB 库存", padding=8)
        inventory.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for column in (1, 3, 5):
            inventory.columnconfigure(column, weight=1)
        ttk.Label(inventory, text="库存").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(
            inventory, textvariable=self._var("wb_stock", "0"), width=14,
        ).grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(
            inventory, text="读取仓库", command=lambda: self._run(self._wb_load_warehouses),
        ).grid(row=0, column=4, columnspan=2, sticky="e", padx=4)
        ttk.Label(inventory, text="WB 仓库").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        self.wb_warehouse_combo = ttk.Combobox(
            inventory, textvariable=self._var("wb_warehouse_display"), state="readonly",
        )
        self.wb_warehouse_combo.grid(row=1, column=1, columnspan=5, sticky="ew", padx=4, pady=3)
        self.wb_warehouse_combo.bind("<<ComboboxSelected>>", self._wb_warehouse_selected)
        self._var("wb_warehouse_id")
        ttk.Label(
            inventory,
            text="商品卡片创建、图片、价格均为真实 API 操作；Sandbox 模式会跳过真实仓库库存。",
            foreground="#8a5a00",
        ).grid(row=2, column=0, columnspan=6, sticky="w", padx=4, pady=(5, 0))

    def _wb_connection_settings(self) -> dict:
        return {
            "token": self.vars["wb_token"].get().strip(),
            "sandbox": self.vars["wb_sandbox"].get().strip() == "1",
        }

    def _wb_api_from_ui(self) -> WildberriesApi:
        settings = self._ui_call(self._wb_connection_settings)
        return WildberriesApi(settings["token"], sandbox=settings["sandbox"])

    def _refresh_wb_config_state(self):
        configured = bool(self.vars.get("wb_token") and self.vars["wb_token"].get().strip())
        if configured:
            mode = "Sandbox" if self.vars["wb_sandbox"].get() == "1" else "正式环境"
            seller = self.vars.get("wb_seller_name")
            suffix = f"；{seller.get()}" if seller is not None and seller.get().strip() else ""
            self.wb_connection_var.set(f"Token 已配置；{mode}{suffix}")
            self.wb_submit_button.state(["!disabled"])
        else:
            self.wb_connection_var.set("尚未配置 WB API Token")
            self.wb_submit_button.state(["disabled"])
        self._render_wb_job()

    def _save_wb_config(self):
        try:
            settings = self._wb_connection_settings()
            WildberriesApi(settings["token"], sandbox=settings["sandbox"])
            atomic_write_json(self.WB_CONFIG_PATH, self._config())
        except Exception as error:
            messagebox.showerror("WB 配置保存失败", str(error))
            return
        self._refresh_wb_config_state()
        messagebox.showinfo("WB 配置已保存", f"WB Token 已保存在本机：{self.WB_CONFIG_PATH}")

    def _test_wb_connection(self):
        api = self._wb_api_from_ui()
        pings = api.ping()
        seller = api.seller_info()
        seller_name = str(seller.get("name") or "WB 卖家")
        seller_id = str(seller.get("sid") or seller.get("id") or "")

        def update():
            self._var("wb_seller_name").set(seller_name)
            self._var("wb_seller_id").set(seller_id)
            atomic_write_json(self.WB_CONFIG_PATH, self._config())
            self._refresh_wb_config_state()
            messagebox.showinfo(
                "WB 连接成功",
                f"卖家：{seller_name}\n已验证：{', '.join(pings)}\n"
                "上架还需要 Content、Prices and Discounts、Marketplace 的读写权限。",
            )

        self._ui_call(update)

    @staticmethod
    def _wb_parent_identity(item: dict) -> tuple[int, str]:
        return int(item.get("id") or item.get("parentID") or 0), str(
            item.get("name") or item.get("parentName") or "未命名父类目"
        )

    @staticmethod
    def _wb_subject_identity(item: dict) -> tuple[int, str]:
        return int(item.get("subjectID") or item.get("id") or 0), str(
            item.get("subjectName") or item.get("name") or "未命名子类目"
        )

    def _wb_load_parents(self):
        query = self._ui_call(lambda: self.vars["wb_parent_display"].get().strip())
        if query in self.wb_parent_by_display:
            query = ""
        parents = self._wb_api_from_ui().parent_categories()
        if query:
            parents = [item for item in parents if query.casefold() in
                       " ".join(map(str, self._wb_parent_identity(item))).casefold()]

        def update():
            self.wb_parents = parents
            self.wb_parent_by_display = {}
            for item in parents:
                parent_id, name = self._wb_parent_identity(item)
                if parent_id:
                    self.wb_parent_by_display[f"{name} [{parent_id}]"] = item
            values = list(self.wb_parent_by_display)
            self.wb_parent_combo.configure(values=values)
            self.wb_status_var.set(f"已读取 {len(values)} 个 WB 父类目")

        self._ui_call(update)

    def _wb_selected_parent(self) -> tuple[int, str]:
        display = self.vars["wb_parent_display"].get().strip()
        item = self.wb_parent_by_display.get(display, {})
        if item:
            return self._wb_parent_identity(item)
        try:
            return int(self.vars["wb_parent_id"].get()), display
        except ValueError:
            return 0, display

    def _wb_load_subjects(self):
        parent_id, _display = self._ui_call(self._wb_selected_parent)
        if not parent_id:
            raise ValueError("请先选择 WB 父类目")
        subjects = self._wb_api_from_ui().subjects(parent_id=parent_id)
        self._ui_call(lambda: self._wb_apply_subjects(subjects, parent_id))

    def _wb_search_subjects(self):
        parent_id, _display = self._ui_call(self._wb_selected_parent)
        search_text = self._ui_call(lambda: self.vars["wb_subject_display"].get().strip())
        if search_text in self.wb_subject_by_display:
            search_text = ""
        subjects = self._wb_api_from_ui().subjects(
            parent_id=parent_id or None, name=search_text,
        )
        self._ui_call(lambda: self._wb_apply_subjects(subjects, parent_id))

    def _wb_apply_subjects(self, subjects: list[dict], parent_id=0):
        self.wb_subjects = subjects
        self.wb_subject_by_display = {}
        for item in subjects:
            subject_id, name = self._wb_subject_identity(item)
            if subject_id:
                self.wb_subject_by_display[f"{name} [{subject_id}]"] = item
        self.wb_subject_combo.configure(values=list(self.wb_subject_by_display))
        if parent_id:
            self.vars["wb_parent_id"].set(str(parent_id))
        self.wb_status_var.set(f"已读取 {len(subjects)} 个 WB 子类目")

    def _wb_subject_selected(self, _event=None):
        display = self.vars["wb_subject_display"].get().strip()
        item = self.wb_subject_by_display.get(display)
        if not item:
            return
        subject_id, _name = self._wb_subject_identity(item)
        try:
            current_subject_id = int(self.vars["wb_subject_id"].get().strip())
        except ValueError:
            current_subject_id = 0
        if current_subject_id == subject_id:
            return
        self.vars["wb_subject_id"].set(str(subject_id))
        self.wb_characteristics = []
        self.wb_characteristic_values = {}
        self._wb_refresh_characteristics_tree()
        self._wb_sync_content_json(show_error=False)

    def _wb_selected_subject_id(self) -> int:
        self._wb_subject_selected()
        try:
            subject_id = int(self.vars["wb_subject_id"].get().strip())
        except ValueError as error:
            raise ValueError("请先选择有效的 WB 子类目") from error
        if subject_id <= 0:
            raise ValueError("请先选择有效的 WB 子类目")
        return subject_id

    def _wb_load_characteristics(self):
        subject_id = self._ui_call(self._wb_selected_subject_id)
        characteristics = self._wb_api_from_ui().subject_characteristics(subject_id)

        def update():
            if self.vars["wb_subject_id"].get().strip() != str(subject_id):
                return
            self.wb_characteristics = characteristics
            self.wb_characteristic_values = {
                str(key): value for key, value in self.wb_characteristic_values.items()
                if any(str(item.get("charcID")) == str(key) for item in characteristics)
            }
            self._wb_refresh_characteristics_tree()
            self._wb_sync_content_json(show_error=False)
            missing = sum(1 for item in characteristics if item.get("required"))
            self.wb_status_var.set(f"类目属性 {len(characteristics)} 项；必填 {missing} 项")

        self._ui_call(update)

    def _wb_refresh_characteristics_tree(self):
        tree = getattr(self, "wb_attribute_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        for item in self.wb_characteristics:
            charc_id = str(item.get("charcID") or "")
            if not charc_id:
                continue
            value = self.wb_characteristic_values.get(charc_id, "")
            rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
            required = bool(item.get("required"))
            tree.insert(
                "", tk.END, iid=charc_id,
                values=(
                    item.get("name") or charc_id,
                    "是" if required else "否",
                    item.get("charcType") if item.get("charcType") is not None else "",
                    item.get("unitName") or "",
                    rendered,
                ),
                tags=("required",) if required and not rendered.strip() else (),
            )

    def _wb_selected_characteristic(self) -> dict:
        selection = self.wb_attribute_tree.selection()
        if not selection:
            raise ValueError("请先在属性表中选择一项")
        charc_id = str(selection[0])
        return next(
            (item for item in self.wb_characteristics if str(item.get("charcID")) == charc_id),
            {},
        )

    def _wb_attribute_selected(self, _event=None):
        try:
            item = self._wb_selected_characteristic()
        except ValueError:
            return
        charc_id = str(item.get("charcID"))
        self.wb_selected_attribute_var.set(f"{item.get('name') or charc_id} [{charc_id}]")
        value = self.wb_characteristic_values.get(charc_id, "")
        self.vars["wb_attribute_value"].set(
            json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
        )
        self.vars["wb_directory_display"].set("")
        self.wb_directory_combo.configure(values=[])

    def _wb_save_characteristic(self):
        try:
            item = self._wb_selected_characteristic()
        except Exception as error:
            messagebox.showwarning("未选择属性", str(error))
            return
        charc_id = str(item.get("charcID"))
        raw_value = self.vars["wb_attribute_value"].get().strip()
        if raw_value:
            self.wb_characteristic_values[charc_id] = raw_value
        else:
            self.wb_characteristic_values.pop(charc_id, None)
        self._wb_refresh_characteristics_tree()
        self._wb_sync_content_json(show_error=False)

    def _wb_clear_characteristic(self):
        try:
            item = self._wb_selected_characteristic()
        except ValueError:
            return
        self.wb_characteristic_values.pop(str(item.get("charcID")), None)
        self.vars["wb_attribute_value"].set("")
        self._wb_refresh_characteristics_tree()
        self._wb_sync_content_json(show_error=False)

    @staticmethod
    def _wb_directory_kind(name: str) -> str:
        normalized = str(name or "").casefold()
        mappings = (
            ("colors", ("color", "цвет", "颜色")),
            ("kinds", ("gender", "kind", "пол", "性别")),
            ("countries", ("country", "страна", "国家")),
            ("seasons", ("season", "сезон", "季节")),
            ("vat", ("vat", "ндс", "增值税", "税率")),
            ("tnved", ("tnved", "тнвэд", "海关", "商品编码")),
        )
        for kind, needles in mappings:
            if any(needle in normalized for needle in needles):
                return kind
        raise ValueError("该属性没有可识别的 WB 官方字典，请手工填写属性值")

    def _wb_load_directory_options(self):
        item = self._ui_call(self._wb_selected_characteristic)
        kind = self._wb_directory_kind(str(item.get("name") or ""))
        subject_id = self._ui_call(self._wb_selected_subject_id)
        values = self._wb_api_from_ui().directory_values(kind, subject_id=subject_id)

        def update():
            self.wb_directory_by_display = {}
            for raw in values:
                if isinstance(raw, dict):
                    value = next((
                        raw.get(key) for key in ("name", "value", "title", "tnved", "id")
                        if raw.get(key) not in (None, "")
                    ), raw)
                    display = str(value)
                    if display in self.wb_directory_by_display:
                        display += " | " + json.dumps(raw, ensure_ascii=False)
                else:
                    value = raw
                    display = str(raw)
                self.wb_directory_by_display[display] = value
            choices = list(self.wb_directory_by_display)
            self.wb_directory_combo.configure(values=choices)
            if choices:
                self.wb_directory_combo.current(0)
            self.wb_status_var.set(f"已读取 {len(choices)} 个官方属性选项")

        self._ui_call(update)

    def _wb_use_directory_value(self):
        display = self.vars["wb_directory_display"].get().strip()
        if display not in self.wb_directory_by_display:
            messagebox.showwarning("未选择选项", "请先加载并选择一个 WB 官方选项")
            return
        self.vars["wb_attribute_value"].set(
            json.dumps([self.wb_directory_by_display[display]], ensure_ascii=False)
        )
        self._wb_save_characteristic()

    def _wb_content_form_inputs(self) -> dict:
        return {
            "subject_id": self.vars["wb_subject_id"].get().strip(),
            "vendor_code": self.vars["wb_vendor_code"].get().strip(),
            "title": self.vars["wb_title"].get().strip(),
            "description": self.wb_description_text.get("1.0", tk.END).strip(),
            "brand": self.vars["wb_brand"].get().strip(),
            "barcode": self.vars["wb_barcode"].get().strip(),
            "tech_size": self.vars["wb_tech_size"].get().strip(),
            "wb_size": self.vars["wb_size"].get().strip(),
            "characteristic_definitions": list(self.wb_characteristics),
            "characteristic_values": dict(self.wb_characteristic_values),
        }

    def _wb_write_content_json(self, document: dict):
        if not hasattr(self, "wb_content_json_text"):
            return
        self.wb_content_json_text.delete("1.0", tk.END)
        self.wb_content_json_text.insert(
            "1.0", json.dumps(document, ensure_ascii=False, indent=2),
        )

    def _wb_sync_content_json(self, *, show_error=True):
        try:
            document = build_card_content_document(self._wb_content_form_inputs())
        except Exception as error:
            if show_error:
                messagebox.showwarning("暂时无法生成 JSON", str(error))
            return None
        self._wb_write_content_json(document)
        return document

    def _wb_apply_content_values(self, values: dict, document: dict | None = None):
        for key in ("vendor_code", "title", "brand", "barcode", "tech_size", "wb_size"):
            self.vars["wb_" + key].set(str(values.get(key) or ""))
        self.wb_description_text.delete("1.0", tk.END)
        self.wb_description_text.insert("1.0", str(values.get("description") or ""))
        self.wb_characteristic_values = dict(values.get("characteristic_values") or {})
        self._wb_refresh_characteristics_tree()
        if document is None:
            document = build_card_content_document(self._wb_content_form_inputs())
        self._wb_write_content_json(document)
        missing = [
            str(item.get("name") or item.get("charcID"))
            for item in self.wb_characteristics
            if item.get("required") and not self.wb_characteristic_values.get(str(item.get("charcID")))
        ]
        if missing:
            self.wb_content_json_status_var.set(
                f"已应用内容；仍有 {len(missing)} 项必填属性需要人工核对",
            )
        else:
            self.wb_content_json_status_var.set("内容已应用，当前必填属性均已有值")

    def _wb_apply_content_json(self):
        try:
            document = json.loads(self.wb_content_json_text.get("1.0", tk.END))
            subject_id = self._wb_selected_subject_id()
            values = parse_card_content_document(
                document, self.wb_characteristics, expected_subject_id=subject_id,
            )
            self._wb_apply_content_values(values, document)
        except Exception as error:
            messagebox.showerror("WB 内容 JSON 无法应用", str(error))

    def _wb_read_and_fill_content(self):
        subject_id = self._ui_call(self._wb_selected_subject_id)
        vendor_code = self._ui_call(lambda: self.vars["wb_vendor_code"].get())
        self._wb_load_characteristics()
        self._wb_ai_fill_content(subject_id, vendor_code)

    def _wb_ai_fill_content(self, expected_subject, expected_vendor):
        snapshot = self._ui_call(lambda: {
            "subject_id": self._wb_selected_subject_id(),
            "subject_name": self.vars["wb_subject_display"].get().strip(),
            "definitions": list(self.wb_characteristics),
            "image_paths": list(self.wb_image_paths),
            "document": build_card_content_document(self._wb_content_form_inputs()),
            "source_context": {
                "source_reference": dict(self.wb_source_reference),
                "supplier_1688_url": self.vars["wb_supplier_1688_url"].get().strip(),
            },
            "config": self._analysis_config(),
        })
        if (snapshot["subject_id"] != expected_subject or
                snapshot["document"]["variants"][0]["vendorCode"] != expected_vendor.strip()):
            raise ValueError("当前商品或类目已切换，请重新填写")
        service = WbCardAiService(**snapshot["config"])
        self._log(
            f"开始使用 {min(len(snapshot['image_paths']), service.MAX_IMAGES)} 张图片填写 WB 内容和类目属性",
        )
        document = service.generate(
            subject_id=snapshot["subject_id"],
            subject_name=snapshot["subject_name"],
            definitions=snapshot["definitions"],
            image_paths=snapshot["image_paths"],
            current_document=snapshot["document"],
            source_context=snapshot["source_context"],
        )
        values = parse_card_content_document(
            document, snapshot["definitions"], expected_subject_id=snapshot["subject_id"],
        )

        def update():
            if build_card_content_document(self._wb_content_form_inputs()) != snapshot["document"]:
                raise ValueError("等待 AI 期间商品内容已变化，请重新填写")
            self._wb_apply_content_values(values, document)
            self.wb_status_var.set("WB 图片 AI 已填写内容；请重点核对红色必填属性和尺码")

        self._ui_call(update)
        self._log("WB AI 填写完成，结果已写入可编辑 JSON")

    def _wb_on_listing_mode_changed(self):
        if not hasattr(self, "wb_source_entry"):
            return
        mode = self.vars["wb_listing_mode"].get().strip() or "local"
        if mode not in {"follow", "local"}:
            mode = "local"
            self.vars["wb_listing_mode"].set(mode)
        is_follow = mode == "follow"
        self.wb_source_entry.configure(state="normal" if is_follow else "disabled")
        self.wb_source_parse_button.state(["!disabled"] if is_follow else ["disabled"])
        image_buttons = (
            getattr(self, "wb_image_add_button", None),
            getattr(self, "wb_image_remove_button", None),
            getattr(self, "wb_image_up_button", None),
            getattr(self, "wb_image_down_button", None),
        )
        for button in image_buttons:
            if button is not None:
                button.state(["disabled"] if is_follow else ["!disabled"])

        has_job = bool(isinstance(self.wb_job, dict) and self.wb_job.get("task_id"))
        for button in getattr(self, "wb_listing_mode_buttons", []):
            button.state(["disabled"] if has_job else ["!disabled"])
        if is_follow:
            source_id = self.vars.get("wb_source_nm_id")
            if source_id is not None and source_id.get().strip():
                self.wb_source_status_var.set(
                    f"跟卖模式：已解析 WB 商品 {source_id.get().strip()}，来源图片将用于新商品卡片"
                )
            else:
                self.wb_source_status_var.set("跟卖模式：填写 WB 链接或商品 ID 后解析来源商品")
        else:
            self.wb_source_status_var.set("本地新品模式：不需要跟卖链接，只使用自己准备的成品图片")

    @staticmethod
    def _wb_calculate_pricing(inputs: dict) -> WbPricingBreakdown:
        return calculate_wb_price(
            fulfillment_mode=inputs.get("fulfillment_mode"),
            purchase_cost_cny=inputs.get("purchase_cost"),
            label_fee_cny=inputs.get("label_fee"),
            target_profit_margin_percent=inputs.get("target_profit_margin_percent"),
            sales_commission_percent=inputs.get("sales_commission_percent"),
            advertising_percent=inputs.get("advertising_percent"),
            cargo_loss_percent=inputs.get("cargo_loss_percent"),
            cny_rub_rate=inputs.get("cny_rub_rate"),
            weight_kg=inputs.get("weight_kg"),
            length_cm=inputs.get("length_cm"),
            width_cm=inputs.get("width_cm"),
            height_cm=inputs.get("height_cm"),
            discount_percent=inputs.get("discount", 0),
            first_mile_shipping_cny=inputs.get("first_mile_shipping", 0),
            warehouse_operation_fee_cny=inputs.get("warehouse_operation_fee", 0),
            logistics_coefficient_percent=inputs.get("logistics_coefficient_percent", 100),
            localization_index=inputs.get("localization_index", 1),
        )

    def _wb_pricing_form_values(self) -> dict[str, str]:
        keys = (
            "fulfillment_mode", "purchase_cost", "label_fee",
            "target_profit_margin_percent", "sales_commission_percent",
            "advertising_percent", "cargo_loss_percent", "cny_rub_rate",
            "weight_kg", "length_cm", "width_cm", "height_cm", "discount",
            "first_mile_shipping", "warehouse_operation_fee",
            "logistics_coefficient_percent", "localization_index",
        )
        return {
            key: self.vars["wb_" + key].get().strip()
            for key in keys
        }

    @staticmethod
    def _wb_money(value: Decimal) -> str:
        return format(value.quantize(Decimal("0.01")), "f")

    def _wb_update_price(self, *_args):
        if self._wb_pricing_trace_suspended or not hasattr(self, "wb_pricing_status_var"):
            return None
        mode = self.vars["wb_pricing_mode"].get() if "wb_pricing_mode" in self.vars else "cross_border"
        for widget in getattr(self, "wb_auto_pricing_widgets", []):
            widget.grid_remove() if mode == "manual" else widget.grid()
        if mode == "manual":
            self.wb_cross_border_pricing.grid_remove()
            self.wb_overseas_pricing.grid_remove()
        if mode in {"manual", "overseas_warehouse"}:
            self.wb_pricing_breakdown = None
            self.wb_price_entry.configure(state="normal" if mode == "manual" else "readonly")
            if mode == "manual":
                self.wb_price_currency_var.set(f"手动售价({self.vars['wb_manual_currency'].get()})")
                self.wb_pricing_status_var.set("直接采用输入售价；多变体采用各行售价，不校验成本、佣金和利润率")
            else:
                self.vars["wb_price"].set("")
                self.wb_pricing_status_var.set("海外仓运费公式待配置，暂不可自动核价或提交；可选择直接设置售价")
                self.wb_overseas_fee_var.set("海外仓物流：等待运费公式")
            return None
        self.wb_price_entry.configure(state="readonly")
        try:
            result = self._wb_calculate_pricing(self._wb_pricing_form_values())
        except Exception as error:
            self.wb_pricing_breakdown = None
            self._wb_pricing_trace_suspended = True
            try:
                self.vars["wb_price"].set("")
            finally:
                self._wb_pricing_trace_suspended = False
            self.wb_pricing_status_var.set(str(error))
            if hasattr(self, "wb_cross_border_fee_var"):
                self.wb_cross_border_fee_var.set("标准跨境运费：待计算")
            if hasattr(self, "wb_overseas_fee_var"):
                self.wb_overseas_fee_var.set("海外仓物流：待计算")
            return None

        self.wb_pricing_breakdown = result
        self._wb_pricing_trace_suspended = True
        try:
            self.vars["wb_price"].set(format(result.list_price, "f"))
        finally:
            self._wb_pricing_trace_suspended = False
        if result.fulfillment_mode == "cross_border":
            self.wb_cross_border_fee_var.set(
                f"标准跨境运费：{self._wb_money(result.cross_border_shipping_cny)} CNY"
                f"（计费重 {format(result.billable_weight_kg, 'f')}kg）"
            )
        else:
            head_cny = result.first_mile_shipping_cny + result.warehouse_operation_fee_cny
            self.wb_overseas_fee_var.set(
                f"头程合计 {self._wb_money(head_cny)} CNY | "
                f"WB 尾程 {self._wb_money(result.tail_delivery_rub)} RUB | "
                f"包装体积 {self._wb_money(result.volume_liters)} L"
            )
        self.wb_pricing_status_var.set(
            f"折后 {self._wb_money(result.discounted_price)} {result.currency_code} | "
            f"总物流 {self._wb_money(result.logistics_total)} {result.currency_code} | "
            f"利润 {self._wb_money(result.profit)} {result.currency_code} | "
            f"利润率 {self._wb_money(result.actual_profit_margin_percent)}%"
        )
        return result

    def _wb_on_pricing_mode_changed(self, *_args):
        mode = self.vars["wb_pricing_mode"].get()
        if getattr(self, "_wb_last_pricing_mode", "") == "manual":
            self.vars["wb_manual_price"].set(self.vars["wb_price"].get())
        if mode != "manual":
            self.vars["wb_fulfillment_mode"].set(mode)
        elif getattr(self, "_wb_last_pricing_mode", "") != "manual":
            self.vars["wb_price"].set(self.vars["wb_manual_price"].get())
        self._wb_last_pricing_mode = mode
        self._wb_on_fulfillment_mode_changed()

    @staticmethod
    def _wb_apply_listing_price(inputs):
        mode = inputs.get("pricing_mode")
        if mode == "manual":
            currency = inputs.get("manual_currency") or "CNY"
            if currency not in {"CNY", "RUB"}:
                raise ValueError("请选择手动售价币种 CNY 或 RUB")
            try:
                price = Decimal(str(inputs.get("price") or "").strip())
            except InvalidOperation as error:
                raise ValueError("手动售价必须是大于 0 的整数") from error
            if not price.is_finite() or price <= 0 or price != price.to_integral_value():
                raise ValueError("手动售价必须是大于 0 的整数")
            inputs["price"] = format(price, "f")
            inputs["currency_code"] = currency
            inputs["pricing_breakdown"] = {}
            return
        if mode == "overseas_warehouse":
            raise ValueError("海外仓运费公式待配置，请暂用直接设置售价模式")
        if mode not in (None, "", "cross_border"):
            raise ValueError("请选择有效的定价模式")
        if mode == "cross_border":
            inputs["fulfillment_mode"] = "cross_border"
        pricing = WbUiMixin._wb_calculate_pricing(inputs)
        inputs.update(price=format(pricing.list_price, "f"), currency_code=pricing.currency_code,
                      pricing_breakdown=pricing.json_values())

    def _wb_on_fulfillment_mode_changed(self, *_args):
        if not hasattr(self, "wb_pricing_status_var"):
            return
        mode = self.vars["wb_fulfillment_mode"].get().strip() or "cross_border"
        if mode not in {"cross_border", "overseas_warehouse"}:
            mode = "cross_border"
            self.vars["wb_fulfillment_mode"].set(mode)
        currency_code = "RUB" if mode == "overseas_warehouse" else "CNY"
        if hasattr(self, "wb_price_currency_var"):
            self.wb_price_currency_var.set(f"自动标价({currency_code})")
        if hasattr(self, "wb_cross_border_pricing"):
            if mode == "overseas_warehouse":
                self.wb_cross_border_pricing.grid_remove()
                self.wb_overseas_pricing.grid(row=3, column=0, columnspan=8, sticky="ew")
            else:
                self.wb_overseas_pricing.grid_remove()
                self.wb_cross_border_pricing.grid(row=3, column=0, columnspan=8, sticky="ew")
        self._wb_update_price()

    def _wb_parse_follow_source(self):
        source = self._ui_call(lambda: self.vars["wb_source_url"].get().strip())
        mode = self._ui_call(lambda: self.vars["wb_listing_mode"].get().strip())
        if mode != "follow":
            raise ValueError("只有 WB 跟卖模式需要解析来源商品")
        canonical_url, nm_id = normalize_wb_source(source)
        self._log(f"开始解析 WB 来源商品：{canonical_url}")
        product = scrape_wb_source(
            canonical_url,
            profile_dir=self.OUTPUT_DIR.parent / "browser_profile_wb",
            log_func=self._log,
        )
        image_dir = (
            self.OUTPUT_DIR / "wb-source" / nm_id /
            f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        )
        image_paths = download_wb_source_images(
            product.images, image_dir, log_func=self._log,
        )
        reference = product.to_dict()
        reference["downloaded_image_paths"] = list(image_paths)

        def update():
            if self.vars["wb_listing_mode"].get().strip() != "follow":
                return
            self.wb_source_reference = reference
            self.vars["wb_source_url"].set(product.url)
            self.vars["wb_source_nm_id"].set(product.nm_id)
            self.vars["wb_title"].set(product.title)
            if product.brand:
                self.vars["wb_brand"].set(product.brand)
            self.wb_description_text.delete("1.0", tk.END)
            self.wb_description_text.insert("1.0", product.description)
            self._wb_set_image_paths(image_paths)
            self.wb_source_status_var.set(
                f"已解析 WB 商品 {product.nm_id}：{len(image_paths)} 张来源图片；请核对文案、类目和属性"
            )
            self.wb_status_var.set("WB 跟卖来源已准备，等待补充类目、属性、价格和库存")

        self._ui_call(update)
        self._log(f"WB 来源商品解析完成：nmID={nm_id}，图片 {len(image_paths)} 张")

    def _wb_set_image_paths(self, paths):
        unique = []
        seen = set()
        for raw in paths:
            path = str(Path(raw))
            key = path.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
            if len(unique) >= 30:
                break
        self.wb_image_paths = unique
        self.wb_image_list.delete(0, tk.END)
        for index, path in enumerate(unique, start=1):
            self.wb_image_list.insert(tk.END, f"{index:02d}. {path}")
        mode = self.vars.get("wb_listing_mode")
        prefix = "来源" if mode is not None and mode.get().strip() == "follow" else "本地"
        self.wb_image_status_var.set(
            f"已准备 {len(unique)} 张{prefix}图片；第 1 张为主图"
            if unique else f"尚未准备{prefix}图片；第 1 张作为主图"
        )

    def _wb_select_images(self):
        if self.vars["wb_listing_mode"].get().strip() != "local":
            messagebox.showwarning("当前为跟卖模式", "跟卖模式请通过“解析来源商品”准备图片")
            return
        paths = filedialog.askopenfilenames(
            title="选择 WB 商品图片（第 1 张为主图）",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.webp *.bmp *.gif")],
        )
        if paths:
            self._wb_set_image_paths([*self.wb_image_paths, *paths])

    def _wb_remove_image(self):
        selection = self.wb_image_list.curselection()
        if selection:
            paths = list(self.wb_image_paths)
            paths.pop(selection[0])
            self._wb_set_image_paths(paths)

    def _wb_move_image(self, offset: int):
        selection = self.wb_image_list.curselection()
        if not selection:
            return
        old_index = selection[0]
        new_index = max(0, min(len(self.wb_image_paths) - 1, old_index + offset))
        if new_index == old_index:
            return
        paths = list(self.wb_image_paths)
        paths.insert(new_index, paths.pop(old_index))
        self._wb_set_image_paths(paths)
        self.wb_image_list.selection_set(new_index)

    def _wb_load_warehouses(self):
        api = self._wb_api_from_ui()
        warehouses = api.warehouses()

        def update():
            self.wb_warehouse_by_display = {}
            for item in warehouses:
                warehouse_id = int(item.get("id") or 0)
                if warehouse_id:
                    display = f"{item.get('name') or '未命名仓库'} [{warehouse_id}]"
                    self.wb_warehouse_by_display[display] = item
            values = list(self.wb_warehouse_by_display)
            self.wb_warehouse_combo.configure(values=values)
            self.wb_variant_warehouse_combo.configure(values=values)
            if self.vars["wb_warehouse_display"].get() not in values:
                self.vars["wb_warehouse_display"].set("")
                self.vars["wb_warehouse_id"].set("")
            if api.sandbox:
                self.vars["wb_warehouse_id"].set("")
                self.vars["wb_warehouse_display"].set("Sandbox 不写真实库存")
                self.wb_status_var.set("Sandbox 模式已跳过真实仓库读取")
            else:
                self.wb_status_var.set(f"已读取 {len(values)} 个 WB 卖家仓库")

        self._ui_call(update)

    def _wb_warehouse_selected(self, _event=None):
        item = self.wb_warehouse_by_display.get(self.vars["wb_warehouse_display"].get().strip())
        if item:
            self.vars["wb_warehouse_id"].set(str(item.get("id") or ""))

    def _wb_collect_form_snapshot(self) -> dict:
        self._wb_subject_selected()
        self._wb_warehouse_selected()
        keys = (
            "wb_listing_mode", "wb_source_url", "wb_source_nm_id",
            "wb_vendor_code", "wb_supplier_1688_url", "wb_title", "wb_brand",
            "wb_subject_id", "wb_subject_display", "wb_length_cm", "wb_width_cm",
            "wb_height_cm", "wb_weight_kg", "wb_tech_size", "wb_size", "wb_barcode",
            "wb_price", "wb_discount", "wb_warehouse_id", "wb_warehouse_display", "wb_stock",
            "wb_seller_name", "wb_seller_id", "wb_fulfillment_mode",
            "wb_pricing_mode", "wb_manual_currency",
            "wb_sales_commission_percent",
            "wb_purchase_cost", "wb_label_fee", "wb_target_profit_margin_percent",
            "wb_advertising_percent", "wb_cargo_loss_percent", "wb_cny_rub_rate",
            "wb_first_mile_shipping", "wb_warehouse_operation_fee",
            "wb_logistics_coefficient_percent", "wb_localization_index",
        )
        values = {key.removeprefix("wb_"): self.vars[key].get().strip() for key in keys}
        values["description"] = self.wb_description_text.get("1.0", tk.END).strip()
        values["characteristic_definitions"] = list(self.wb_characteristics)
        values["characteristic_values"] = dict(self.wb_characteristic_values)
        values["source_reference"] = dict(self.wb_source_reference)
        if values["listing_mode"] != "follow":
            values["source_url"] = ""
            values["source_nm_id"] = ""
            values["source_reference"] = {}
        return values

    @staticmethod
    def _wb_validate_listing_inputs(inputs: dict, *, sandbox: bool):
        listing_mode = str(inputs.get("listing_mode") or "local").strip()
        if listing_mode not in {"follow", "local"}:
            raise ValueError("WB 上品模式无效，请重新选择跟卖模式或本地新品模式")
        if listing_mode == "follow":
            source_url, source_nm_id = normalize_wb_source(str(inputs.get("source_url") or ""))
            supplied_nm_id = str(inputs.get("source_nm_id") or "").strip()
            if supplied_nm_id and supplied_nm_id != source_nm_id:
                raise ValueError("WB 来源链接与已解析商品 ID 不一致，请重新解析来源商品")
            reference = inputs.get("source_reference")
            reference_nm_id = str(reference.get("nm_id") or "").strip() if isinstance(reference, dict) else ""
            if reference_nm_id != source_nm_id:
                raise ValueError("请先点击“解析来源商品”，再开始 WB 跟卖上架")
            inputs["source_url"] = source_url
            inputs["source_nm_id"] = source_nm_id
        fulfillment_mode = str(inputs.get("fulfillment_mode") or "cross_border").strip()
        if fulfillment_mode not in {"cross_border", "overseas_warehouse"}:
            raise ValueError("WB 核价方式无效，请重新选择跨境发货或海外仓发货")
        WbUiMixin._wb_apply_listing_price(inputs)
        preview = dict(inputs)
        preview["barcode"] = preview.get("barcode") or "PREVIEW-BARCODE"
        build_card_payload(preview)
        try:
            price = Decimal(str(inputs.get("price") or "").strip())
            discount = int(str(inputs.get("discount") or "0").strip())
            stock = int(str(inputs.get("stock") or "0").strip())
        except (InvalidOperation, ValueError) as error:
            raise ValueError("WB 售价、折扣和库存必须是有效整数") from error
        if not price.is_finite() or price <= 0 or price != price.to_integral_value():
            raise ValueError(f"WB 售价必须是大于 0 的整数 {inputs['currency_code']}")
        if discount < 0 or discount >= 100:
            raise ValueError("WB 折扣必须在 0 到 99 之间")
        if stock < 0:
            raise ValueError("WB 库存不能小于 0")
        if not sandbox:
            try:
                warehouse_id = int(str(inputs.get("warehouse_id") or "").strip())
            except ValueError as error:
                raise ValueError("正式环境必须选择 WB 卖家仓库") from error
            if warehouse_id <= 0:
                raise ValueError("正式环境必须选择 WB 卖家仓库")

    def _wb_copy_stable_images(self, image_paths: list[str], vendor_code: str) -> list[str]:
        safe_vendor = re.sub(r"[^A-Za-z0-9._-]+", "-", vendor_code).strip("-.") or "wb-product"
        target = self.OUTPUT_DIR / "wb-products" / safe_vendor / uuid.uuid4().hex
        target.mkdir(parents=True, exist_ok=True)
        copied = []
        for index, raw_path in enumerate(validate_image_paths(image_paths, check_resolution=False), start=1):
            source = Path(raw_path)
            suffix = source.suffix.casefold() or ".jpg"
            destination = target / f"{index:02d}{suffix}"
            with Image.open(source) as image:
                if image.width >= 700 and image.height >= 900:
                    shutil.copy2(source, destination)
                else:
                    canvas_size = (max(700, min(image.width, 1600)), max(900, min(image.height, 2000)))
                    oriented = ImageOps.exif_transpose(image).convert("RGBA")
                    fitted = ImageOps.contain(oriented, canvas_size, Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", canvas_size, "white")
                    canvas.paste(fitted, ((canvas.width - fitted.width) // 2,
                                          (canvas.height - fitted.height) // 2), fitted)
                    destination = destination.with_suffix(".jpg")
                    canvas.save(destination, quality=94)
                    self._log(f"WB 图片已适配：{source.name} {image.width}×{image.height} → {canvas.width}×{canvas.height}")
            copied.append(str(destination))
        return validate_image_paths(copied)

    def _confirm_wb_submit(self):
        if self.wb_product_tabs.index(self.wb_product_tabs.select()) == 3 and self.wb_variant_rows:
            if messagebox.askyesno("确认多变体上架", f"将运行 {len(self.wb_variant_rows)} 个变体的上架流程，继续吗？"):
                self._run(self._wb_run_variants)
            return
        if not self.vars["wb_token"].get().strip():
            messagebox.showwarning("未配置 WB", "请先填写并保存 WB API Token")
            return
        listing_label = (
            "跟卖模式" if self.vars["wb_listing_mode"].get().strip() == "follow" else "本地新品模式"
        )
        fulfillment_label = (
            "海外仓发货"
            if self.vars["wb_fulfillment_mode"].get().strip() == "overseas_warehouse"
            else "跨境发货"
        )
        if not messagebox.askyesno(
            "确认 WB 上架",
            f"当前为 {listing_label} / {fulfillment_label}，人工佣金 "
            f"{self.vars['wb_sales_commission_percent'].get().strip() or '未填写'}%，"
            f"目标利润率 {self.vars['wb_target_profit_margin_percent'].get().strip() or '未填写'}%，"
            f"售价 {self.vars['wb_price'].get().strip() or '未计算'}（币种见定价面板）。\n"
            "将通过 WB API 创建或继续商品卡片，并上传图片、设置价格和库存。\n"
            "程序会按卖家货号防止重复创建。确认继续吗？",
        ):
            return
        self._run(self._wb_run_pipeline)

    def _wb_run_pipeline(self):
        self._wb_task_log("开始单商品任务：校验资料")
        try:
            result = self._wb_run_pipeline_impl()
            self._wb_task_log("单商品上架完成")
            return result
        except Exception as error:
            self._wb_task_log(f"单商品任务失败 / 暂停：{error}")
            raise

    def _wb_run_pipeline_impl(self):
        settings = self._ui_call(self._wb_connection_settings)
        inputs = self._ui_call(self._wb_collect_form_snapshot)
        selected_images = self._ui_call(lambda: list(self.wb_image_paths))
        if not self.wb_job or self.wb_job.get("stage", "new") == "new":
            subject = int(inputs["subject_id"])
            definitions = WildberriesApi(settings["token"], sandbox=settings["sandbox"]).subject_characteristics(subject)
            inputs["characteristic_definitions"] = definitions
            validate_image_paths(selected_images, check_resolution=False)
            self._wb_task_log("AI 正在生成 WB 标题、描述和属性")
            generated = WbCardAiService(**self._ui_call(self._analysis_config)).generate(
                subject_id=subject, subject_name=inputs["subject_display"],
                definitions=definitions, image_paths=selected_images,
                current_document=build_card_content_document(inputs),
                source_context=inputs.get("source_reference") or {},
            )
            parsed = parse_card_content_document(generated, definitions, expected_subject_id=subject)
            for key in ("title", "description", "characteristic_values", "tech_size", "wb_size"):
                inputs[key] = parsed.get(key)
            def apply_generated():
                self.wb_characteristics = definitions
                self._wb_apply_content_values(inputs)
            self._ui_call(apply_generated)
        self._wb_validate_listing_inputs(inputs, sandbox=settings["sandbox"])
        validate_image_paths(selected_images, check_resolution=False)
        vendor_code = inputs["vendor_code"]
        state = self.wb_job if isinstance(self.wb_job, dict) else {}
        if state:
            existing_vendor = str((state.get("inputs") or {}).get("vendor_code") or "")
            if existing_vendor != vendor_code:
                raise ValueError(
                    f"当前未结束任务的货号是 {existing_vendor}；请继续该任务或先点击“重置当前任务”"
                )
            if state.get("stage") == "completed":
                raise ValueError("该 WB 任务已经完成；新建商品前请点击“重置当前任务”")
            stage = state.get("stage") or "new"
            if stage == "new":
                state["version"] = 4
                state["inputs"] = inputs
                state["image_paths"] = self._wb_copy_stable_images(selected_images, vendor_code)
            elif stage in {"card_submitted", "card_ready", "media_uploaded"}:
                state["inputs"].update({
                    key: inputs[key] for key in (
                        "price", "discount", "fulfillment_mode", "pricing_mode", "manual_currency", "currency_code", "purchase_cost",
                        "label_fee", "target_profit_margin_percent",
                        "sales_commission_percent", "advertising_percent",
                        "cargo_loss_percent", "cny_rub_rate", "weight_kg",
                        "length_cm", "width_cm", "height_cm",
                        "first_mile_shipping", "warehouse_operation_fee",
                        "logistics_coefficient_percent", "localization_index",
                        "pricing_breakdown",
                    )
                })
                state["version"] = 4
            elif stage == "price_ready":
                state["inputs"].update({
                    key: inputs[key] for key in ("warehouse_id", "warehouse_display", "stock")
                })
        else:
            state = {
                "version": 4,
                "task_id": uuid.uuid4().hex,
                "stage": "new",
                "status": "queued",
                "inputs": inputs,
                "image_paths": self._wb_copy_stable_images(selected_images, vendor_code),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        self.wb_job = state
        self._save_wb_job(state)
        api = WildberriesApi(settings["token"], sandbox=settings["sandbox"])
        workflow = WbListingWorkflow(
            api, save_state=self._save_wb_job, log=self._wb_task_log,
        )
        try:
            result = workflow.advance(state)
            while result.get("stage") != "completed":
                time.sleep(60)
                result = workflow.advance(state)
        except Exception as error:
            state["status"] = "failed"
            state["error"] = str(error) or type(error).__name__
            self._save_wb_job(state)
            raise
        if result.get("stage") == "completed" and not result.get("ledger_completed"):
            ledger = upsert_product_ledger(
                self.PRODUCT_LEDGER_PATH, self._wb_product_ledger_record(result),
            )
            result["ledger_completed"] = True
            result["ledger_path"] = ledger["path"]
            self._save_wb_job(result)
            self._log(f"WB 产品台账已更新：{ledger['path']}（第 {ledger['row']} 行）")
            self._ui_call(lambda: messagebox.showinfo(
                "WB 上架完成",
                f"nmID：{result.get('nm_id')}\nchrtID：{result.get('chrt_id')}\n"
                f"产品台账：{ledger['path']}",
            ))
        return result

    def _sync_reviewed_wb_job(self, job, snapshot, settings, ai_config):
        try:
            self._advance_reviewed_wb_job(job, snapshot, settings, ai_config)
        except Exception as error:
            with self.auto_job_lock:
                job["wb_error"] = str(error) or type(error).__name__
                self._save_auto_jobs()
            raise

    def _advance_reviewed_wb_job(self, job, snapshot, settings, ai_config):
        def save(state):
            with self.auto_job_lock:
                job["wb_state"] = copy.deepcopy(state)
                job["wb_progress"] = state.get("message") or state["stage"]
                self._save_auto_jobs()

        with self.auto_job_lock:
            job.pop("wb_error", None)
            job["wb_progress"] = "准备商品资料和类目属性"
            self._save_auto_jobs()
        api = WildberriesApi(settings["token"], sandbox=settings["sandbox"])
        state = copy.deepcopy(job.get("wb_state"))
        if not state:
            title = str(snapshot["fields"].get("title") or "").strip()
            description = str(snapshot.get("description") or "").strip()
            if not title or not description:
                raise ValueError("Ozon 文案断点缺少标题或描述")
            inputs = dict(job["inputs"]["wb_sync"])
            subject = int(inputs["subject_id"])
            definitions = api.subject_characteristics(subject)
            inputs.update(vendor_code=job["inputs"]["offer_id"], title=title[:60],
                          description=description[:5000],
                          characteristic_definitions=definitions)
            paths = self._wb_copy_stable_images(snapshot["local_images"], inputs["vendor_code"])
            if definitions:
                generated = WbCardAiService(**ai_config).generate(
                    subject_id=subject, subject_name=inputs["subject"], definitions=definitions,
                    image_paths=paths, current_document=build_card_content_document(inputs),
                    source_context={"ozon_title": title, "reference": snapshot.get("reference"),
                                    "vision_analysis": snapshot.get("vision_analysis")},
                    attributes_only=True,
                )
                parsed = parse_card_content_document(generated, definitions, expected_subject_id=subject)
                for key in ("characteristic_values", "tech_size", "wb_size"):
                    inputs[key] = parsed.get(key)
            build_card_payload(dict(inputs, barcode="preview"))
            state = {"inputs": inputs, "stage": "new", "task_id": job["id"],
                     "image_paths": paths}
            save(state)
        workflow = WbListingWorkflow(api, save_state=save, log=self._wb_task_log)
        while state["stage"] != "completed":
            self._check_auto_job_cancelled(job)
            workflow.advance(state)
            if state["stage"] != "completed":
                for _ in range(60):
                    self._check_auto_job_cancelled(job)
                    time.sleep(1)
        if not state.get("ledger_completed"):
            upsert_product_ledger(self.PRODUCT_LEDGER_PATH, self._wb_product_ledger_record(state))
            state["ledger_completed"] = True
            save(state)

    def _wb_product_ledger_record(self, state: dict) -> dict:
        inputs = state.get("inputs") or {}
        pricing = (
            inputs.get("pricing_breakdown")
            if isinstance(inputs.get("pricing_breakdown"), dict) else {}
        )

        def scaled(key, multiplier):
            value = str(inputs.get(key) or "").strip()
            if not value:
                return ""
            return str(Decimal(value) * Decimal(str(multiplier)))

        seller_id = str(inputs.get("seller_id") or "")
        listing_mode = str(inputs.get("listing_mode") or "local")
        fulfillment_mode = str(inputs.get("fulfillment_mode") or "cross_border")
        return {
            "platform": "Wildberries",
            "shop_name": inputs.get("seller_name") or "WB 卖家",
            "shop_config_id": "wb:" + (seller_id or "default"),
            "listing_mode": "WB 跟卖" if listing_mode == "follow" else "WB 本地新品",
            "offer_id": inputs.get("vendor_code"),
            "supplier_1688_url": inputs.get("supplier_1688_url"),
            "wb_source_url": inputs.get("source_url"),
            "wb_source_nm_id": inputs.get("source_nm_id"),
            "wb_nm_id": state.get("nm_id"),
            "wb_chrt_id": state.get("chrt_id"),
            "wb_subject_id": inputs.get("subject_id"),
            "title": inputs.get("title"),
            "price": inputs.get("price"),
            "currency_code": pricing.get("currency_code") or (
                "RUB" if fulfillment_mode == "overseas_warehouse" else "CNY"
            ),
            "pricing_mode": (
                "WB 海外仓发货（利润率）"
                if fulfillment_mode == "overseas_warehouse"
                else "WB 跨境发货（利润率）"
            ),
            "purchase_cost": inputs.get("purchase_cost"),
            "label_fee": inputs.get("label_fee"),
            "target_profit_margin_percent": inputs.get("target_profit_margin_percent"),
            "actual_profit_margin_percent": pricing.get("actual_profit_margin_percent"),
            "discount_percent": inputs.get("discount"),
            "advertising_percent": inputs.get("advertising_percent"),
            "cargo_loss_percent": inputs.get("cargo_loss_percent"),
            "raw_sales_commission_percent": inputs.get("sales_commission_percent"),
            "sales_commission_percent": inputs.get("sales_commission_percent"),
            "shipping": pricing.get("logistics_total") or pricing.get("logistics_total_rub"),
            "cny_rub_rate": inputs.get("cny_rub_rate"),
            "cross_border_shipping_cny": pricing.get("cross_border_shipping_cny"),
            "first_mile_shipping_cny": pricing.get("first_mile_shipping_cny"),
            "warehouse_operation_fee_cny": pricing.get("warehouse_operation_fee_cny"),
            "tail_delivery_rub": pricing.get("tail_delivery_rub"),
            "volume_liters": pricing.get("volume_liters"),
            "sales_commission": pricing.get("sales_commission") or pricing.get("sales_commission_rub"),
            "advertising": pricing.get("advertising") or pricing.get("advertising_rub"),
            "cargo_loss": pricing.get("cargo_loss") or pricing.get("cargo_loss_rub"),
            "profit": pricing.get("profit") or pricing.get("profit_rub"),
            "gross_weight_g": scaled("weight_kg", 1000),
            "depth_mm": scaled("length_cm", 10),
            "width_mm": scaled("width_cm", 10),
            "height_mm": scaled("height_cm", 10),
            "category_display": inputs.get("subject_display"),
            "category_id": inputs.get("subject_id"),
            "warehouse_id": inputs.get("warehouse_id"),
            "stock": inputs.get("stock"),
            "status": "WB 商品、图片、价格及库存完成",
        }

    def _save_wb_job(self, state: dict):
        self.wb_job = state
        atomic_write_json(self.WB_JOB_PATH, state)
        self._wb_task_state(state)
        root = getattr(self, "root", None)
        if root is not None and not getattr(self, "_closing", False):
            if threading.current_thread() is threading.main_thread():
                self._render_wb_job()
            else:
                root.after(0, self._render_wb_job)

    def _load_wb_job(self):
        state = load_json(self.WB_JOB_PATH, {})
        self.wb_job = state if isinstance(state, dict) and state.get("task_id") else {}
        if self.wb_job:
            self._apply_wb_job_inputs(self.wb_job)
        self._render_wb_job()

    def _apply_wb_job_inputs(self, state: dict):
        inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
        for key, value in inputs.items():
            variable = self.vars.get("wb_" + key)
            if variable is not None and key not in {
                "description", "characteristic_definitions", "characteristic_values", "source_reference",
            }:
                variable.set(str(value or ""))
        self.wb_source_reference = dict(inputs.get("source_reference") or {})
        self.wb_description_text.delete("1.0", tk.END)
        self.wb_description_text.insert("1.0", str(inputs.get("description") or ""))
        self.wb_characteristics = list(inputs.get("characteristic_definitions") or [])
        self.wb_characteristic_values = dict(inputs.get("characteristic_values") or {})
        self._wb_refresh_characteristics_tree()
        self._wb_set_image_paths(state.get("image_paths") or [])
        self._wb_on_listing_mode_changed()
        self._wb_on_fulfillment_mode_changed()

    def _render_wb_job(self):
        if not hasattr(self, "wb_status_var"):
            return
        state = self.wb_job if isinstance(self.wb_job, dict) else {}
        stage = str(state.get("stage") or "new")
        progress = WbListingWorkflow.STAGES.get(stage, 0)
        self.wb_progress.configure(value=progress)
        if state:
            status = str(state.get("status") or "")
            message = str(state.get("message") or "")
            error = str(state.get("error") or "")
            self.wb_status_var.set(message or f"WB 任务：{stage} / {status}")
            identifiers = []
            if state.get("nm_id"):
                identifiers.append(f"nmID={state['nm_id']}")
            if state.get("chrt_id"):
                identifiers.append(f"chrtID={state['chrt_id']}")
            if error:
                identifiers.append("错误：" + error)
            self.wb_detail_var.set("；".join(identifiers))
            self.wb_submit_button.configure(
                text="已完成" if stage == "completed" else "继续当前任务",
            )
            if stage == "completed":
                self.wb_submit_button.state(["disabled"])
            elif self.vars.get("wb_token") and self.vars["wb_token"].get().strip():
                self.wb_submit_button.state(["!disabled"])
            barcode = str((state.get("inputs") or {}).get("barcode") or "")
            if barcode and not self.vars["wb_barcode"].get().strip():
                self.vars["wb_barcode"].set(barcode)
        else:
            self.wb_detail_var.set("")
            self.wb_submit_button.configure(text="开始上架")
            if self.vars.get("wb_token") and self.vars["wb_token"].get().strip():
                self.wb_status_var.set("WB API 已配置，等待录入")
                self.wb_submit_button.state(["!disabled"])
            else:
                self.wb_status_var.set("API 尚未配置")
                self.wb_submit_button.state(["disabled"])
        self._wb_on_listing_mode_changed()
        self._wb_on_fulfillment_mode_changed()

    def _reset_wb_job(self):
        if self.wb_job and not messagebox.askyesno(
            "重置 WB 任务",
            "这只会清除本机的 WB 断点记录，不会删除 WB 后台已经创建的商品。确认重置吗？",
        ):
            return
        self.wb_job = {}
        try:
            Path(self.WB_JOB_PATH).unlink(missing_ok=True)
        except OSError as error:
            messagebox.showerror("无法重置", str(error))
            return
        self.vars["wb_vendor_code"].set(
            f"WB-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        )
        self.vars["wb_barcode"].set("")
        self._wb_on_listing_mode_changed()
        self._render_wb_job()
