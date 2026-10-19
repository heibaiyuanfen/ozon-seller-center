from __future__ import annotations

import queue
import threading
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageOps, ImageTk

from supplier_1688 import SupplierWorkspace, collect_1688_images, normalize_1688_url


class SupplierPanel(ttk.Frame):
    def __init__(self, parent, root, on_apply, on_batch=None, on_image_service=None):
        super().__init__(parent, padding=8)
        self.workspace = SupplierWorkspace(root)
        self.on_apply = on_apply
        self.on_batch = on_batch
        self.on_image_service = on_image_service
        self.share_main = tk.BooleanVar(value=False)
        self.preview_path = None
        self.events = queue.Queue()
        self.product = self.variant = None
        self.preview_photo = None
        self.thumbnail_images = []
        self.asset_filter = tk.StringVar(value="全部")
        self.url = tk.StringVar()
        self.status = tk.StringVar(value="")
        self.products = tk.StringVar()
        self.fields = {key: tk.StringVar() for key in ("name", "offer_id", "model_name", "purchase_cost", "weight", "depth", "width", "height")}
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="1688 商品链接").pack(side=tk.LEFT)
        ttk.Entry(bar, textvariable=self.url).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.collect_button = ttk.Button(bar, text="采集图片", command=lambda: self.guarded(self.collect))
        self.collect_button.pack(side=tk.LEFT)
        self.product_combo = ttk.Combobox(self, textvariable=self.products, state="readonly")
        self.product_combo.pack(fill=tk.X, pady=6)
        self.product_combo.bind("<<ComboboxSelected>>", lambda event: self.guarded(self.select_product))
        record_tools = ttk.Frame(self)
        record_tools.pack(fill=tk.X)
        ttk.Button(record_tools, text="清除当前产品记录", command=lambda: self.guarded(self.clear_product_record)).pack(side=tk.LEFT)
        ttk.Button(record_tools, text="恢复产品记录", command=lambda: self.guarded(self.restore_product_records)).pack(side=tk.LEFT, padx=4)
        panes = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True)
        left, center, right = (ttk.Frame(panes, padding=4) for _ in range(3))
        for frame, weight in ((left, 1), (center, 2), (right, 2)):
            panes.add(frame, weight=weight)
        ttk.Label(left, text="产品变体").pack(anchor="w")
        self.variants = tk.Listbox(left, exportselection=False, height=7)
        self.variants.pack(fill=tk.X)
        self.variants.bind("<<ListboxSelect>>", lambda event: self.guarded(self.select_variant))
        commands = ttk.Frame(left)
        commands.pack(fill=tk.X, pady=4)
        for label, action in (("新增", lambda: self.new_variant(False)), ("复制", lambda: self.new_variant(True)), ("清除记录", self.delete_variant), ("恢复记录", self.restore_variant_records)):
            ttk.Button(commands, text=label, command=lambda action=action: self.guarded(action)).pack(side=tk.LEFT)
        labels = {"name": "变体名称", "offer_id": "货号", "model_name": "合并型号", "purchase_cost": "采购成本", "weight": "包装重量(g)", "depth": "包装长(mm)", "width": "包装宽(mm)", "height": "包装高(mm)"}
        for key, variable in self.fields.items():
            ttk.Label(left, text=labels[key]).pack(anchor="w", pady=(4, 0))
            ttk.Entry(left, textvariable=variable).pack(fill=tk.X)
            if key == "offer_id":
                ttk.Button(left, text="重新生成货号", command=lambda: self.guarded(self.regenerate_offer)).pack(anchor="e")
        ttk.Button(left, text="保存变体", command=lambda: self.guarded(self.commit)).pack(fill=tk.X, pady=6)
        ttk.Button(left, text="载入自动工作台", command=lambda: self.guarded(self.apply)).pack(fill=tk.X)
        ttk.Button(left, text="全部变体批量上架", command=lambda: self.guarded(self.batch_apply)).pack(fill=tk.X, pady=4)
        ttk.Label(center, text="采集图片 / 本地素材").pack(anchor="w")
        filters = ttk.Combobox(center, textvariable=self.asset_filter, state="readonly", values=("全部", "候选", "规格", "详情", "本地", "AI主图"))
        filters.pack(fill=tk.X, pady=4)
        filters.bind("<<ComboboxSelected>>", lambda event: self.guarded(self.refresh_assets))
        ttk.Style(self).configure("SupplierAssets.Treeview", rowheight=76)
        self.assets = ttk.Treeview(center, columns=("group", "size"), show="tree headings", selectmode="extended", height=8, style="SupplierAssets.Treeview")
        self.assets.heading("#0", text="图片")
        self.assets.column("#0", width=150)
        self.assets.heading("group", text="分组")
        self.assets.column("group", width=70)
        self.assets.heading("size", text="尺寸")
        self.assets.column("size", width=90)
        scrollbar = ttk.Scrollbar(center, command=self.assets.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.assets.configure(yscrollcommand=scrollbar.set)
        self.assets.pack(fill=tk.BOTH, expand=True)
        self.assets.bind("<<TreeviewSelect>>", lambda event: self.guarded(self.preview_asset))
        for label, action in (("添加本地图片", self.import_images), ("加入当前变体", self.assign_images)):
            ttk.Button(center, text=label, command=lambda action=action: self.guarded(action)).pack(fill=tk.X, pady=3)
        delete_tools = ttk.Frame(center)
        delete_tools.pack(fill=tk.X)
        ttk.Button(delete_tools, text="删除选中素材", command=lambda: self.guarded(self.delete_assets)).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(delete_tools, text="撤销上次删除", command=lambda: self.guarded(self.restore_assets)).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.assets.bind("<Delete>", lambda event: self.guarded(self.delete_assets))
        self.preview_label = ttk.Label(right, anchor="center")
        self.preview_label.pack(fill=tk.BOTH, expand=True)
        ttk.Label(right, text="当前变体上传图片").pack(anchor="w")
        self.chosen = tk.Listbox(right, exportselection=False, height=8)
        self.chosen.pack(fill=tk.X)
        self.chosen.bind("<<ListboxSelect>>", lambda event: self.guarded(self.preview_chosen))
        tools = ttk.Frame(right)
        tools.pack(fill=tk.X, pady=4)
        for label, action in (("设为主图", lambda: self.move_image(None)), ("上移", lambda: self.move_image(-1)), ("下移", lambda: self.move_image(1)), ("移除", self.remove_image)):
            ttk.Button(tools, text=label, command=lambda action=action: self.guarded(action)).pack(side=tk.LEFT)
        edits = ttk.Frame(right)
        edits.pack(fill=tk.X)
        for label, action in (("旋转90°", lambda: self.edit_image("rotate")), ("裁剪", lambda: self.edit_image("crop"))):
            ttk.Button(edits, text=label, command=lambda action=action: self.guarded(action)).pack(side=tk.LEFT)
        self.generate_button = ttk.Button(edits, text="AI 生成主图", command=lambda: self.guarded(self.generate_main))
        self.generate_button.pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(right, text="所有变体共用生成的主图", variable=self.share_main).pack(anchor="w", pady=4)
        ttk.Label(self, textvariable=self.status, wraplength=950).pack(fill=tk.X, pady=5)
        self.refresh_products()
        self._poll_id = self.after(200, self.poll)

    def destroy(self):
        if getattr(self, "_poll_id", None):
            self.after_cancel(self._poll_id)
            self._poll_id = None
        super().destroy()

    def guarded(self, action):
        try:
            action()
        except Exception as error:
            messagebox.showerror("1688 图片工作区", str(error), parent=self)

    def commit(self):
        if self.variant is not None:
            self.variant.update({key: variable.get().strip() for key, variable in self.fields.items()})
            self.workspace.ensure_identifiers(self.product, self.variant.get("model_name"))
            self.fields["offer_id"].set(self.variant["offer_id"])
            self.fields["model_name"].set(self.variant["model_name"])
            self.workspace.save()

    def regenerate_offer(self):
        if self.variant is None:
            raise ValueError("请先选择变体")
        self.fields["offer_id"].set(self.workspace.new_offer_id())
        self.commit()
        self.status.set("已生成当前变体的新货号，其他变体货号保持不变")

    def refresh_products(self):
        items = [item for item in self.workspace.data["products"] if not item.get("record_hidden")]
        self.visible_products = items
        self.product_combo["values"] = [f"{i + 1}. {item['title']}" for i, item in enumerate(items)]
        if items:
            self.product_combo.current(len(items) - 1)
            self.select_product()
        else:
            self.products.set("")
            self.product = self.variant = None
            self.variants.delete(0, tk.END)
            self.assets.delete(*self.assets.get_children())
            self.chosen.delete(0, tk.END)
            for variable in self.fields.values():
                variable.set("")
            self.preview_label.configure(image="")
            self.preview_photo = None

    def select_product(self):
        self.commit()
        index = self.product_combo.current()
        if index < 0:
            return
        self.product = self.visible_products[index]
        self.workspace.ensure_identifiers(self.product)
        self.workspace.save()
        self.preview_path = None
        self.variant = None
        self.url.set(self.product["url"])
        self.refresh_assets()
        self.refresh_variants(0)

    def refresh_assets(self):
        self.assets.delete(*self.assets.get_children())
        self.thumbnail_images.clear()
        if self.product is None:
            return
        for index, asset in enumerate(self.product["assets"]):
            if self.asset_filter.get() not in ("全部", asset.get("group", "候选")):
                continue
            kwargs = {}
            try:
                with Image.open(asset["path"]) as image:
                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((64, 64))
                    thumbnail = ImageTk.PhotoImage(image.copy())
                self.thumbnail_images.append(thumbnail)
                kwargs["image"] = thumbnail
            except (OSError, ValueError):
                pass
            self.assets.insert("", "end", iid=str(index), text=f"{index + 1:03d} {asset.get('label', '')}", values=(asset.get("group", "候选"), f"{asset['width']}x{asset['height']}"), **kwargs)

    def refresh_variants(self, index):
        self.variants.delete(0, tk.END)
        self.visible_variants = [variant for variant in self.product["variants"] if not variant.get("record_hidden")]
        for variant in self.visible_variants:
            self.variants.insert(tk.END, variant["name"])
        self.variant = None
        if self.visible_variants:
            self.variants.selection_set(min(index, len(self.visible_variants) - 1))
            self.select_variant()
        else:
            self.chosen.delete(0, tk.END)
            for variable in self.fields.values():
                variable.set("")

    def select_variant(self):
        selected = self.variants.curselection()
        if not selected:
            return
        self.commit()
        self.variant = self.visible_variants[selected[0]]
        self.preview_path = None
        for key, variable in self.fields.items():
            variable.set(self.variant.get(key, ""))
        self.refresh_chosen()

    def refresh_chosen(self):
        self.chosen.delete(0, tk.END)
        for index, path in enumerate(self.variant["images"]):
            self.chosen.insert(tk.END, f"{'主图' if index == 0 else index + 1}  {Path(path).name}")

    def new_variant(self, duplicate):
        if self.product is None:
            raise ValueError("请先采集商品")
        self.commit()
        self.workspace.add_variant(self.product, self.variant if duplicate else None)
        self.refresh_variants(len(self.product["variants"]) - 1)

    def delete_variant(self):
        if self.variant and messagebox.askyesno("清除变体记录", "隐藏当前变体记录？变体数据和图片保留，可恢复。", parent=self):
            self.commit()
            self.variant["record_hidden"] = True
            self.workspace.save()
            self.refresh_variants(0)

    def restore_variant_records(self):
        if self.product:
            self.commit()
            for variant in self.product["variants"]:
                variant.pop("record_hidden", None)
            self.workspace.save()
            self.refresh_variants(0)

    def clear_product_record(self):
        if self.product and messagebox.askyesno("清除产品记录", "隐藏当前产品及其变体列表？素材、变体和编辑数据全部保留。", parent=self):
            self.commit()
            self.product["record_hidden"] = True
            self.workspace.save()
            self.variant = None
            self.refresh_products()

    def restore_product_records(self):
        self.commit()
        for product in self.workspace.data["products"]:
            product.pop("record_hidden", None)
        self.workspace.save()
        self.refresh_products()

    def batch_apply(self):
        self.commit()
        if self.product is None or self.on_batch is None:
            raise ValueError("请先选择产品")
        variants = [variant for variant in self.product["variants"] if not variant.get("record_hidden")]
        self.on_batch(self.product, variants)

    def show_image(self, path):
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((380, 300))
            self.preview_photo = ImageTk.PhotoImage(image.copy())
        self.preview_label.configure(image=self.preview_photo)
        self.preview_path = str(path)

    def generate_main(self):
        self.commit()
        if not self.product or not self.variant or not self.on_image_service:
            raise ValueError("请先选择变体并配置 AI 生图服务")
        reference = self.preview_path or next(iter(self.variant["images"]), None)
        if not reference or not Path(reference).is_file():
            raise ValueError("请先点击一张素材或变体图片作为参考图")
        targets = [v["id"] for v in self.product["variants"] if not v.get("record_hidden")] if self.share_main.get() else [self.variant["id"]]
        product_id = self.product["id"]
        service = self.on_image_service({"offer_id": self.variant["offer_id"], "request_id": uuid.uuid4().hex}, lambda text: self.events.put(("log", text)))
        output = self.workspace.root / "generated" / uuid.uuid4().hex
        prompt = ("Create one clean professional ecommerce main image, portrait 3:4. "
                  "Preserve the exact product, color, structure and quantity from the reference. "
                  "Use a clean background and show the whole product clearly. No text, logos, "
                  "watermarks, website addresses, QR codes or invented accessories.")
        self.generate_button.configure(state=tk.DISABLED)
        self.status.set("正在生成一张 AI 主图")
        def worker():
            try:
                paths = service.generate([reference], prompt, 1, str(output), exact_3_4=True)
                if len(paths) != 1:
                    raise ValueError("生图服务未返回一张主图")
                with Image.open(paths[0]) as image:
                    image.load()
                    if image.width * 4 != image.height * 3 or min(image.size) < 600:
                        raise ValueError("主图必须为 3:4，短边至少 600 像素")
                self.events.put(("generated", (product_id, targets, paths[0])))
            except Exception as error:
                self.events.put(("generation_error", str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def receive_main(self, payload):
        product_id, targets, path = payload
        self.commit()
        product = next(p for p in self.workspace.data["products"] if p["id"] == product_id)
        self.workspace.set_generated_main(product, targets, path)
        if self.product is product:
            self.refresh_assets()
            if self.variant and self.variant["id"] in targets:
                self.refresh_chosen()
                self.show_image(path)
        self.status.set(f"AI 主图已保存，应用到 {len(targets)} 个变体；附图保留")

    def preview_asset(self):
        selected = self.assets.selection()
        if selected:
            self.show_image(self.product["assets"][int(selected[0])]["path"])

    def preview_chosen(self):
        selected = self.chosen.curselection()
        if selected and self.variant:
            self.show_image(self.variant["images"][selected[0]])

    def delete_assets(self):
        selected = self.assets.selection()
        if not selected or self.product is None:
            return
        if not messagebox.askyesno("删除素材", f"从素材列表删除选中的 {len(selected)} 张图片？\n已分配给变体的图片会保留，可撤销本次删除。", parent=self):
            return
        self.commit()
        count = self.workspace.delete_assets(self.product, selected)
        self.preview_label.configure(image="")
        self.preview_photo = None
        self.refresh_assets()
        self.status.set(f"已删除 {count} 张素材；原文件保留，可撤销删除")

    def restore_assets(self):
        if self.product is None:
            return
        count = self.workspace.restore_assets(self.product)
        self.refresh_assets()
        self.status.set(f"已恢复 {count} 张素材")

    def assign_images(self):
        if self.variant is None:
            raise ValueError("请先选择变体")
        paths = list(self.variant["images"])
        for key in self.assets.selection():
            path = self.product["assets"][int(key)]["path"]
            if path not in paths:
                paths.append(path)
        if len(paths) > 15:
            raise ValueError("每个变体最多上传 15 张图片，请先减少选择")
        self.variant["images"] = paths
        self.commit()
        self.refresh_chosen()

    def move_image(self, offset):
        selected = self.chosen.curselection()
        if not selected:
            return
        index = selected[0]
        target = 0 if offset is None else max(0, min(len(self.variant["images"]) - 1, index + offset))
        paths = self.variant["images"]
        paths.insert(target, paths.pop(index))
        self.commit()
        self.refresh_chosen()
        self.chosen.selection_set(target)

    def remove_image(self):
        selected = self.chosen.curselection()
        if selected:
            self.variant["images"].pop(selected[0])
            self.commit()
            self.refresh_chosen()

    def edit_image(self, operation):
        selected = self.chosen.curselection()
        if not selected:
            raise ValueError("请先选择当前变体中的图片")
        index = selected[0]
        path = self.variant["images"][index]
        box = None
        if operation == "crop":
            with Image.open(path) as image:
                width, height = ImageOps.exif_transpose(image).size
            value = simpledialog.askstring("裁剪", f"原图 {width}x{height}；填写左,上,右,下坐标", initialvalue=f"0,0,{width},{height}", parent=self)
            if value is None:
                return
            box = tuple(int(part.strip()) for part in value.split(","))
            if len(box) != 4:
                raise ValueError("请填写四个整数坐标")
        edited = self.workspace.edit_image(path, operation, box)
        self.variant["images"][index] = edited
        self.commit()
        self.refresh_chosen()
        self.chosen.selection_set(index)
        self.show_image(edited)

    def import_images(self):
        if self.product is None:
            raise ValueError("请先采集商品")
        paths = filedialog.askopenfilenames(parent=self, filetypes=[("图片", "*.jpg *.jpeg *.png *.webp")])
        for path in paths:
            with Image.open(path) as image:
                self.product["assets"].append({"path": path, "label": Path(path).name, "group": "本地", "width": image.width, "height": image.height})
        self.commit()
        self.select_product()

    def apply(self):
        self.commit()
        if self.variant is None or not self.variant["images"]:
            raise ValueError("请为当前变体选择上传图片")
        self.on_apply(self.product, self.variant)

    def collect(self):
        url = normalize_1688_url(self.url.get())
        self.commit()
        self.collect_button.configure(state=tk.DISABLED)
        self.status.set("正在打开采集浏览器")
        def worker():
            try:
                result = collect_1688_images(url, self.workspace.root / "originals", self.workspace.root / "browser-profile", log=lambda text: self.events.put(("log", text)))
                self.events.put(("product", result))
            except Exception as error:
                self.events.put(("error", str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "log":
                    self.status.set(payload)
                elif event in ("generated", "generation_error"):
                    self.generate_button.configure(state=tk.NORMAL)
                    try:
                        if event == "generation_error":
                            raise RuntimeError(payload)
                        self.receive_main(payload)
                    except Exception as error:
                        self.status.set(str(error))
                        messagebox.showerror("AI 主图生成失败", str(error), parent=self)
                else:
                    self.collect_button.configure(state=tk.NORMAL)
                    if event == "product":
                        self.workspace.add_product(payload)
                        self.refresh_products()
                        self.status.set(f"已采集 {len(payload['assets'])} 张候选图，失败 {payload['failures']} 张")
                    else:
                        self.status.set(payload)
                        messagebox.showerror("1688 采集失败", payload, parent=self)
        except queue.Empty:
            pass
        self._poll_id = self.after(200, self.poll)
