from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from core import load_json, atomic_write_json
from shared_catalog_client import CatalogClient
from catalog_upload import CatalogOutbox


def save_catalog_settings(root, address):
    target = address.strip()
    if target:
        if not target.lower().startswith('https://'):
            raise ValueError('共享资料服务地址必须使用 HTTPS')
        client = CatalogClient(target)
        target = client.base_url
        client.close()
    atomic_write_json(Path(root) / 'shared-catalog-settings.json',
                      {'base_url': target, 'enabled': bool(target)})
    return target


def build_catalog_settings(app, parent, root):
    frame = ttk.LabelFrame(parent, text='共享资料自动上传', padding=6)
    frame.columnconfigure(1, weight=1)
    config = load_json(Path(root) / 'shared-catalog-settings.json', {})
    app.catalog_url_var = tk.StringVar(value=config.get('base_url', ''))
    ttk.Label(frame, text='服务 HTTPS 根地址').grid(row=0, column=0, padx=5)
    ttk.Entry(frame, textvariable=app.catalog_url_var, width=55).grid(row=0, column=1, sticky='ew')
    def save():
        try:
            target = save_catalog_settings(root, app.catalog_url_var.get())
            app.catalog_url_var.set(target)
            messagebox.showinfo('配置已保存', '已启用上架后自动上传；上传失败不影响上架，可从台账补传。' if target else '已关闭共享资料自动上传。')
        except Exception as error:
            messagebox.showerror('设置未保存', str(error))
    ttk.Button(frame, text='保存上传配置', command=save).grid(row=0, column=2, padx=5)
    ttk.Button(frame, text='连接测试 / 失败补传', command=lambda: open_catalog_connection(app, root)).grid(row=0, column=3, padx=5)
    ttk.Label(frame, text='保存地址即自动启用；留空保存即关闭。图片上传 OSS，资料在上架和台账完成后自动上传，失败仅记录日志。\n共享库中的采购成本、商品名称、规格、尺寸及图片链接公开可读。', foreground='#666').grid(row=1, column=0, columnspan=4, sticky='w', pady=4)
    ttk.Button(frame, text='查看上传记录（自动刷新）', command=lambda: open_upload_monitor(app, root)).grid(row=2, column=0, columnspan=2, sticky='w')
    return frame


def open_upload_monitor(app, root):
    window = tk.Toplevel(app.root)
    window.title('共享资料上传记录 · 每 5 秒刷新')
    window.geometry('1100x420')
    summary = tk.StringVar()
    ttk.Label(window, textvariable=summary).pack(anchor='w', padx=10, pady=8)
    columns = ('sku', 'status', 'product_id', 'time', 'target', 'detail')
    tree = ttk.Treeview(window, columns=columns, show='headings')
    for key, label in zip(columns, ('货号', '上传状态', '服务端产品ID', '更新时间（UTC）', '服务器', '说明')):
        tree.heading(key, text=label)
        tree.column(key, width=170)
    tree.pack(fill=tk.BOTH, expand=True, padx=10)
    ttk.Label(window, text='成功以服务端回执为准；无回执可能是待传或响应丢失。补传请使用“连接测试 / 失败补传”，无需重新上架。').pack(padx=10, pady=8)
    timer = None
    def refresh():
        nonlocal timer
        rows = CatalogOutbox(root).records()
        selected = tree.selection()
        position = tree.yview()
        tree.delete(*tree.get_children())
        for index, record in enumerate(rows):
            tree.insert('', tk.END, iid=str(index), values=[record.get(key, '') for key in columns])
        for key in selected:
            if tree.exists(key):
                tree.selection_add(key)
        if position:
            tree.yview_moveto(position[0])
        success = sum(bool(row.get('product_id')) for row in rows)
        summary.set(f'共 {len(rows)} 条 · 成功 {success} · 待处理 {len(rows)-success}。每 5 秒读取本地记录，不发起上传。')
        timer = window.after(5000, refresh)
    def close():
        if timer:
            window.after_cancel(timer)
        window.destroy()
    window.protocol('WM_DELETE_WINDOW', close)
    refresh()


def open_catalog_connection(app, root):
    settings_path = Path(root) / 'shared-catalog-settings.json'
    config = load_json(settings_path, {})
    window = tk.Toplevel(app.root)
    window.title('共享产品库：资料上传（免登录 v1）')
    url = tk.StringVar(value=config.get('base_url', ''))
    ttk.Label(window, text='服务 HTTPS 根地址').grid(row=0, column=0, padx=10, pady=10)
    ttk.Entry(window, textvariable=url, width=58).grid(row=0, column=1, padx=10)
    ttk.Label(window, text='无需账号或密钥。采购成本、商品名称、规格、尺寸及图片链接入库后公开可读。\n文案、标签、完整属性、售价库存不在接口范围，继续保存在本地台账。', wraplength=620).grid(row=1, column=0, columnspan=2, padx=10, pady=8)
    ttk.Label(window, text='保存 HTTPS 地址后自动上传；失败不影响上架。留空保存可关闭。').grid(row=2, column=0, columnspan=2, padx=10, pady=8)
    def save():
        try:
            target = save_catalog_settings(root, url.get())
            if hasattr(app, 'catalog_url_var'):
                app.catalog_url_var.set(target)
            return target
        except Exception as error:
            messagebox.showerror('设置未保存', str(error), parent=window)
            return None
    def connect():
        target = save()
        if target:
            def worker():
                client = CatalogClient(target)
                try:
                    capability = client.connect()
                    if capability.get('version') != 1 or capability.get('authentication') != 'none':
                        raise ValueError('不是免登录共享产品库 v1')
                    app._log('共享产品库连接成功（免登录）')
                finally:
                    client.close()
            app._run(worker)
    def retry():
        target = save()
        if target and messagebox.askyesno('补传资料', '将原样重试此服务器的待传请求，不重新上架，也不覆盖既有产品。是否继续？', parent=window):
            app._run(lambda: CatalogOutbox(root).retry(target, app._log))
    def ledger():
        target = save()
        if not target:
            return
        path = filedialog.askopenfilename(parent=window, title='选择本地产品台账', filetypes=[('Excel', '*.xlsx')])
        if not path:
            return
        from erp_ledger import read_ledger
        try:
            rows = [(number, row) for number, row in read_ledger(path) if str(row.get('platform') or '').lower() == 'ozon']
        except Exception as error:
            messagebox.showerror('读取失败', str(error), parent=window)
            return
        preview = tk.Toplevel(window)
        preview.title('台账资料补传预览：Ctrl/Shift 多选')
        tree = ttk.Treeview(preview, columns=('shop', 'sku', 'name', 'cost', 'status'), show='headings', selectmode='extended')
        for key, label in zip(tree['columns'], ('店铺', '货号', '名称', '采购成本CNY', '预检')):
            tree.heading(key, text=label)
        tree.pack(fill=tk.BOTH, expand=True)
        outbox = CatalogOutbox(root)
        from catalog_upload import product_data
        for index, (_, row) in enumerate(rows):
            try:
                product_data(row, 'preview')
                status = '可补传基础资料（无图链接；不含文案属性）'
            except Exception as error:
                status = str(error)
            tree.insert('', tk.END, iid=str(index), values=(row.get('shop_name'), row.get('offer_id'), row.get('title'), row.get('purchase_cost'), status))
        def upload():
            chosen = [rows[int(key)] for key in tree.selection()]
            if not chosen or not messagebox.askyesno('确认公开上传', f'上传选中的 {len(chosen)} 条基础资料（含采购成本）。文案、标签、属性保留本地，图片留空。不重新上架。是否继续？', parent=preview):
                return
            def worker():
                success = failed = 0
                for number, row in chosen:
                    try:
                        request = outbox.prepare(row, target)
                        outbox.send(request, target)
                        success += 1
                    except Exception:
                        failed += 1
                        app._log(f'台账第 {number} 行待补传：请核对来源身份、字段或已入库冲突；原资料保留')
                app._log(f'台账资料补传：成功 {success}，待处理 {failed}；没有重新执行上架')
            app._run(worker)
        ttk.Button(preview, text='上传选中基础资料', command=upload).pack(pady=10)
    actions = ttk.Frame(window)
    actions.grid(row=3, column=0, columnspan=2, pady=10)
    for label, action in (('保存设置', save), ('测试连接', connect), ('补传待传记录', retry), ('从台账补传基础资料', ledger)):
        ttk.Button(actions, text=label, command=action).pack(side=tk.LEFT, padx=5)
