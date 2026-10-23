"""First-version review UI and bridge to the existing listing queue."""
import copy
import time
import uuid
import tkinter as tk
from tkinter import ttk, messagebox
from pool_review import DEFAULT_RULES, validate_rules, evaluate_product, pricing_values, source_identity, read_listing_history


class PoolReviewMixin:
    def _pool_rules(self):
        return {**DEFAULT_RULES, **getattr(self, "product_pool_rules", {})}

    def _edit_pool_rules(self):
        window = tk.Toplevel(self.root)
        window.title("选品规则（保存后持续生效）")
        window.transient(self.root)
        window.grab_set()
        labels = {
            "category_keyword": "来源类目包含（空＝不限）", "min_sales": "最低销量（空＝不限）",
            "sales_period_days": "销量统计周期（天，不自动折算）", "min_price": "最低市场价（空＝不限）",
            "max_price": "最高市场价（空＝不限）", "max_weight": "最大包装毛重/克（空＝不限）",
            "min_roi": "最低 ROI %（利润/采购与贴单投入）", "min_margin": "最低销售利润率 %（利润/售价）",
            "label_fee": "默认贴单费（沿用现有核价单位）", "advertising_percent": "默认广告费率 %",
            "cargo_loss_percent": "默认货损率 %", "max_age_days": "人工确认有效期（天）",
        }
        variables = {}
        for row, (key, label) in enumerate(labels.items()):
            ttk.Label(window, text=label).grid(row=row, column=0, sticky="w", padx=12, pady=5)
            variables[key] = tk.StringVar(value=self._pool_rules()[key])
            ttk.Entry(window, textvariable=variables[key], width=25).grid(row=row, column=1, padx=12, pady=5)
        def save():
            try:
                rules = validate_rules({key: value.get().strip() for key, value in variables.items()})
                old = getattr(self, "product_pool_rules", {})
                self.product_pool_rules = rules
                try:
                    self._save_product_pool()
                except Exception:
                    self.product_pool_rules = old
                    raise
            except Exception as error:
                messagebox.showerror("规则未保存", str(error), parent=window)
                return
            self._refresh_product_pool_tree()
            window.destroy()
        ttk.Button(window, text="保存规则", command=save).grid(row=len(labels), column=1, pady=12)

    def _selected_pool_items(self):
        return [self.product_pool_products[int(key)] for key in self.product_pool_tree.selection()]

    def _confirm_pool_sales_period(self):
        items = self._selected_pool_items()
        if not items:
            messagebox.showwarning("未选择", "请先多选需要确认销量周期的商品")
            return
        days = self._pool_rules()["sales_period_days"]
        if not messagebox.askyesno("确认数据口径", f"请核对导出表原始统计范围。选中的 {len(items)} 件商品销量确实是 {days} 天统计值吗？\n程序只记录周期，不会把其他周期销量折算成月销。"):
            return
        for item in items:
            item["sales_period_days"] = days
        self._save_product_pool()
        self._refresh_product_pool_tree()

    def _review_pool_selection(self):
        items = self._selected_pool_items()
        if not items:
            messagebox.showwarning("未选择", "请先在选品池多选商品（Ctrl/Shift），再批量审核")
            return
        rules = self._pool_rules()
        fbp = self._job_var("fbp_pricing", "0").get() == "1"
        exchange_rate = self._job_var("shipping_exchange_rate", "12.5").get()
        window = tk.Toplevel(self.root)
        window.title("批量审核 / 反向核价（本地计算，不调用 AI）")
        window.geometry("1150x620")
        ttk.Label(window, text=f"核价：{'FBP' if fbp else 'RFBS'}；ROI ≥ {rules['min_roi']}%；销售利润率 ≥ {rules['min_margin']}%。双击商品可返回编辑，审核后需重新打开此表。",
                  wraplength=1100).pack(fill=tk.X, padx=12, pady=8)
        columns = ("sku", "price", "ceiling", "cost", "profit", "roi", "margin", "status", "reason")
        tree = ttk.Treeview(window, columns=columns, show="headings", selectmode="extended")
        for key, label, width in zip(columns, ("来源SKU", "预计成交价", "最高采购成本", "到手成本", "单件利润", "ROI%", "销售利润率%", "结果", "原因"), (110, 85, 100, 85, 85, 70, 100, 70, 350)):
            tree.heading(key, text=label)
            tree.column(key, width=width, stretch=key == "reason")
        tree.pack(fill=tk.BOTH, expand=True, padx=12)
        scroll = ttk.Scrollbar(window, orient="horizontal", command=tree.xview)
        scroll.pack(fill=tk.X, padx=12)
        tree.configure(xscrollcommand=scroll.set)
        for index, item in enumerate(items):
            result = evaluate_product(item, rules, fbp=fbp, exchange_rate=exchange_rate)
            item["review_result"] = {**result, "reviewed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "rules": dict(rules), "fbp": fbp, "exchange_rate": exchange_rate}
            tree.insert("", tk.END, iid=str(index), values=(item.get("sku"), result["price"], result["ceiling"], item.get("purchase_cost"), result["profit"], result["roi"], result["margin"], result["status"], result["reason"]))
        self._save_product_pool()
        def edit(_event=None):
            if tree.selection():
                item = items[int(tree.selection()[0])]
                self.product_pool_tree.selection_set(str(self.product_pool_products.index(item)))
                self._edit_selected_pool_product()
        tree.bind("<Double-1>", edit)
        def enqueue():
            chosen = [items[int(key)] for key in tree.selection()]
            if not chosen:
                messagebox.showwarning("未选择", "请在审核表选中要入队的商品", parent=window)
                return
            self._enqueue_reviewed_pool_items(chosen, window)
        ttk.Button(window, text="刷新本批审核", command=lambda: (window.destroy(), self._review_pool_selection())).pack(side=tk.LEFT, padx=12, pady=10)
        ttk.Button(window, text="选中商品预检并入队（需最终确认）", command=enqueue).pack(side=tk.RIGHT, padx=12, pady=10)

    def _prepare_reviewed_pool_jobs(self, items, offer_ids=None):
        """Read current inputs again; do not trust a stale UI review or inherit product facts."""
        rules = self._pool_rules()
        base = self._job_inputs_from_form()
        fbp = str(base.get("fbp_pricing") or "0") == "1"
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        jobs, identities, offers = [], set(), set()
        existing = list(self.auto_jobs.values())
        listed_sources, listed_offers = read_listing_history(getattr(self, "PRODUCT_LEDGER_PATH", None))
        for item in items:
            result = evaluate_product(item, rules, fbp=fbp, exchange_rate=base.get("shipping_exchange_rate", "12.5"))
            if result["status"] != "通过":
                raise ValueError(f"{item.get('sku')}: {result['status']}：{result['reason']}")
            inputs = dict(base)
            # Only shop/category/warehouse/image settings come from the workbench.
            for key in ("offer_id", "model_name", "purchase_cost", "old_price", "sales_commission_percent", "net_weight", "weight", "depth", "width", "height", "stock", "supplier_1688_url"):
                inputs[key] = str(item.get(key) or ("0" if key == "old_price" else ""))
            inputs.update(pricing_values(item, rules, fbp))
            inputs.update(listing_mode="follow", source_url=str(item.get("listing_url") or item.get("sku") or ""),
                local_image_paths=[], amazon_generate_main="0", amazon_source="", price=result["price"],
                pool_source_identity=source_identity(item), pool_review=copy.deepcopy(item.get("review_result") or {}),
                supplier_sku=str(item.get("supplier_sku") or ""))
            if not inputs["offer_id"]:
                inputs["offer_id"] = (offer_ids or {}).get(source_identity(item)) or self._new_offer_id()
            group = time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
            for target in self._expand_job_inputs_for_shops(inputs, group):
                identity = (source_identity(item), target["ozon_shop_id"])
                offer = (target["offer_id"], target["ozon_shop_id"])
                if identity in identities or offer in offers:
                    raise ValueError("本批次存在重复商品/店铺或重复货号")
                if identity in listed_sources or offer in listed_offers:
                    raise ValueError(f"{item.get('sku')} 在目标店铺已有产品台账记录，不重复上架")
                for old in existing:
                    old_inputs = old.get("inputs") or {}
                    if old_inputs.get("ozon_shop_id") == target["ozon_shop_id"] and (
                        source_identity(old_inputs) == identity[0] or old_inputs.get("offer_id") == target["offer_id"]):
                        raise ValueError(f"{item.get('sku')} 在目标店铺已有队列记录，请继续原任务或处理历史记录")
                self._validate_job_inputs(target)
                # Existing validation derives an ROI price. Preserve the explicitly reviewed selling price.
                target["price"] = result["price"]
                target["pool_review"] = {**result, "reviewed_at": now, "rules": dict(rules), "fbp": fbp,
                    "confirmed_at": item.get("review_confirmed_at"), "supplier_sku": item.get("supplier_sku")}
                if target.get("old_price") not in ("", "0") and float(target["old_price"]) <= float(target["price"]):
                    raise ValueError("折扣前价格必须高于审核成交价，或填写 0")
                identities.add(identity)
                offers.add(offer)
                jobs.append({"id": group + "-" + uuid.uuid4().hex[:6], "created_at": now, "updated_at": now,
                    "status": "queued", "stage": 0, "failed_stage": 0, "inputs": target, "state": {},
                    "error": "", "message": "批量审核通过，等待运行", "task_id": 0,
                    "import_completed": False, "stock_completed": False, "ledger_completed": False, "cancel_requested": False})
        return jobs

    def _enqueue_reviewed_pool_items(self, items, parent):
        try:
            jobs = self._prepare_reviewed_pool_jobs(items)
        except Exception as error:
            messagebox.showerror("整批未入队，请修正后重试", str(error), parent=parent)
            return
        lines = "\n".join(f"{job['inputs']['ozon_shop_name']} | {job['inputs']['source_url']} | {job['inputs']['offer_id']} | {job['inputs']['price']} RUB | 仓库 {job['inputs']['warehouse_id']}" for job in jobs)
        preview = tk.Toplevel(parent)
        preview.title("最终确认：将真实上架")
        text = tk.Text(preview, width=110, height=22, wrap="word")
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", f"共 {len(items)} 件商品 / {len(jobs)} 个店铺任务。使用自动工作台当前所选类目、店铺、仓库和图片模式。\n\n" + lines)
        text.configure(state="disabled")
        def confirm():
            try:
                # Recheck data and duplicate history immediately before committing.
                offer_ids = {j["inputs"]["pool_source_identity"]: j["inputs"]["base_offer_id"] for j in jobs}
                current = self._prepare_reviewed_pool_jobs(items, offer_ids)
                if len(current) != len(jobs):
                    raise ValueError("目标店铺已变化，请重新审核")
                for left, right in zip(current, jobs):
                    ignored = {"product_group_id", "pool_review"}
                    if {k: v for k, v in left["inputs"].items() if k not in ignored} != {k: v for k, v in right["inputs"].items() if k not in ignored}:
                        raise ValueError("配置或商品数据已变化，请关闭预览重新审核")
                self._persist_reviewed_pool_jobs(jobs)
            except Exception as error:
                messagebox.showerror("未入队", str(error), parent=preview)
                return
            for job in jobs:
                self.auto_job_queue.put(job["id"])
            self._schedule_job_prefetch(jobs)
            self._refresh_job_tree()
            self._start_auto_job_worker()
            preview.destroy()
            parent.destroy()
        ttk.Button(preview, text="确认加入队列并真实上架", command=confirm).pack(side=tk.RIGHT, padx=10, pady=10)
        ttk.Button(preview, text="取消", command=preview.destroy).pack(side=tk.LEFT, padx=10, pady=10)
        preview.transient(parent)
        preview.grab_set()

    def _persist_reviewed_pool_jobs(self, jobs):
        """Commit the complete reviewed batch before any scheduler sees it."""
        with self.auto_job_lock:
            if any(job["id"] in self.auto_jobs for job in jobs):
                raise ValueError("任务编号冲突，请重新预检")
            old_order = list(self.auto_job_order)
            try:
                for job in jobs:
                    self.auto_jobs[job["id"]] = job
                    self.auto_job_order.append(job["id"])
                self._save_auto_jobs()
            except Exception:
                for job in jobs:
                    self.auto_jobs.pop(job["id"], None)
                self.auto_job_order[:] = old_order
                raise
