import tkinter as tk
from tkinter import messagebox, ttk

from erp_ledger import read_ledger, preview_ledger, upload_ledger_report


def open_ledger_preview(app, root, path, client_factory):
    rows = list(read_ledger(path))
    window = tk.Toplevel(app.root)
    window.title('台账上传预览（ERP 1.1）')
    window.geometry('1000x580')
    search = tk.StringVar()
    scope = tk.StringVar(value='当前筛选结果')
    toolbar = ttk.Frame(window, padding=8)
    toolbar.pack(fill=tk.X)
    ttk.Label(toolbar, text='筛选').pack(side=tk.LEFT)
    ttk.Entry(toolbar, textvariable=search, width=35).pack(side=tk.LEFT, padx=5)
    ttk.Combobox(toolbar, textvariable=scope, state='readonly', values=('选中行', '当前筛选结果', '全部跟卖台账'), width=20).pack(side=tk.LEFT)
    tree = ttk.Treeview(window, columns=('row', 'store', 'offer', 'title', 'status'), show='headings', selectmode='extended')
    for key, title, width in (('row', '行', 45), ('store', '店铺', 100), ('offer', '货号', 170), ('title', '名称', 270), ('status', '校验/处理结果', 330)):
        tree.heading(key, text=title)
        tree.column(key, width=width)
    scroll = ttk.Scrollbar(window, command=tree.yview)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(fill=tk.BOTH, expand=True)
    status = tk.StringVar(value='先校验预览，确认后才上传。未知单位不猜测，旧普通草稿不重复创建。')
    ttk.Label(window, textvariable=status, wraplength=950).pack(fill=tk.X, padx=8, pady=8)
    results = {}
    def refresh(*_):
        tree.delete(*tree.get_children())
        term = search.get().strip().casefold()
        for index, (number, row) in enumerate(rows):
            if term and term not in str(row).casefold():
                continue
            tree.insert('', 'end', iid=str(index), values=(number, row.get('shop_name') or '', row.get('offer_id') or '', row.get('title') or '', results.get(number, '待预览')))
    search.trace_add('write', refresh)
    refresh()
    def run():
        ids = tree.selection() if scope.get() == '选中行' else tree.get_children() if scope.get() == '当前筛选结果' else [str(i) for i in range(len(rows))]
        selected = [rows[int(i)] for i in ids]
        if not selected:
            messagebox.showinfo('台账预览', '请选择需要上传的行', parent=window)
            return
        def worker():
            client = client_factory()
            try:
                report = preview_ledger(selected, root, client, app._log)
                details = []
                for item in report['items']:
                    preview = item.get('preview', {})
                    actions = ', '.join(str(entry.get('action')) for entry in preview.get('items', []))
                    conflicts = [c for entry in preview.get('items', []) for c in entry.get('field_conflicts', [])]
                    results[item['row']] = item.get('error') or actions or item['status']
                    if conflicts:
                        results[item['row']] += '；资料差异待确认'
                    details.append(f"第 {item['row']} 行：{results[item['row']]}")
                def confirm():
                    refresh()
                    count = sum(i['status'] == 'ready' for i in report['items'])
                    status.set(f'可上传 {count} 行；缺项、警告和差异详情见日志及 latest-report.json')
                    return count and messagebox.askyesno('确认产品库增量上传', '\n'.join(details[:15]) + f'\n\n上传通过校验的 {count} 行？缺项保持未知，已有非空资料不覆盖。', parent=window)
                if app._ui_call(confirm):
                    upload_ledger_report(report, root, client, app._log)
                    for item in report['items']:
                        results[item['row']] = item.get('error') or ('已合并，资料差异待确认' if item['status'] == 'conflicts' else item['status'])
                    app._ui_call(refresh)
            finally:
                client.close()
        app._run(worker)
    ttk.Button(window, text='校验预览 → 确认上传', command=run).pack(pady=(0, 10))
