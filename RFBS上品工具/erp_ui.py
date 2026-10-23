"""Connection controls; credentials live only in this process/environment."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core import atomic_write_json, load_json
from erp_backup import BackupOutbox, ErpBackupClient, base_url


def open_connection(app, root):
    from catalog_upload_ui import open_catalog_connection
    open_catalog_connection(app, root)


def _archived_open_connection(app, root):
    window = tk.Toplevel(app.root)
    window.title('产品库连接（本机调试）')
    config_path = root / 'erp-backup-settings.json'
    config = load_json(config_path, {})
    enabled = tk.BooleanVar(value=bool(config.get('enabled')))
    fields = {}
    for row, (label, key, default, secret) in enumerate((
        ('产品库根地址', 'url', config.get('base_url', 'http://127.0.0.1:8788'), False),
        ('本地访问密钥', 'ERP_UPLOAD_TOKEN', os.environ.get('ERP_UPLOAD_TOKEN', ''), True),
        ('多人账号', 'ERP_UPLOAD_USERNAME', os.environ.get('ERP_UPLOAD_USERNAME', ''), False),
        ('多人密码', 'ERP_UPLOAD_PASSWORD', os.environ.get('ERP_UPLOAD_PASSWORD', ''), True),
    )):
        ttk.Label(window, text=label).grid(row=row, column=0, padx=8, pady=5, sticky='w')
        fields[key] = tk.StringVar(value=default)
        ttk.Entry(window, textvariable=fields[key], width=52, show='*' if secret else '').grid(row=row, column=1, padx=8, pady=5)
    ttk.Checkbutton(window, text='台账完成后自动校验并备份到产品库', variable=enabled).grid(row=4, column=0, columnspan=2, sticky='w', padx=8)
    ttk.Label(window, text='凭据仅本次运行有效；备份失败不影响上架，可单独重试。').grid(row=5, column=0, columnspan=2, padx=8, pady=5)
    def apply():
        try:
            url = base_url(fields['url'].get())
            if enabled.get() and not messagebox.askyesno('确认自动资料备份', '开启后，每次上架并写台账成功，会将文案、属性、价格及公开图片链接校验后导入此产品库。是否允许？', parent=window):
                return False
            for key in ('ERP_UPLOAD_TOKEN', 'ERP_UPLOAD_USERNAME', 'ERP_UPLOAD_PASSWORD'):
                os.environ[key] = fields[key].get()
            atomic_write_json(config_path, {'enabled': enabled.get(), 'base_url': url})
            return True
        except Exception as error:
            messagebox.showerror('产品库设置', str(error), parent=window)
            return False
    def connect():
        if apply():
            def worker():
                client = client_from_config(load_json(config_path, {}))
                try:
                    client.login()
                    client.call('GET', '/api/v1/catalog/follow-backups/schema?version=1.1.0')
                    app._log('产品库连接与接口权限检查通过')
                finally:
                    client.close()
            app._run(worker)
    def retry():
        if apply():
            app._run(lambda: retry_pending(root, app._log))
    def upload_ledger():
        path = filedialog.askopenfilename(parent=window, title='选择产品台账', initialdir=root,
                                         filetypes=[('Excel 台账', '*.xlsx')])
        if not path or not messagebox.askyesno('上传现有产品台账', '使用 1.1 增量协议。为无货号行保存稳定身份，将在 Excel 新增隐藏记录ID列，不改业务字段；请先关闭 Excel。之后可选行、筛选并预览。是否继续？', parent=window):
            return
        if apply():
            try:
                from erp_ledger import ensure_row_ids
                from erp_ledger_ui import open_ledger_preview
                ensure_row_ids(path)
                saved = load_json(config_path, {})
                open_ledger_preview(app, root, path, lambda: client_from_config(saved))
            except Exception as error:
                messagebox.showerror('台账预览', str(error), parent=window)
    actions = ttk.Frame(window)
    actions.grid(row=6, column=0, columnspan=2, pady=8)
    ttk.Button(actions, text='保存连接', command=apply).pack(side=tk.LEFT, padx=4)
    ttk.Button(actions, text='测试连接', command=connect).pack(side=tk.LEFT, padx=4)
    ttk.Button(actions, text='重试待备份记录', command=retry).pack(side=tk.LEFT, padx=4)
    ttk.Button(window, text='上传现有产品台账（空字段留空）', command=upload_ledger).grid(row=7, column=0, columnspan=2, pady=(0, 10))


def client_from_config(config):
    return ErpBackupClient(config.get('base_url', 'http://127.0.0.1:8788'),
        os.environ.get('ERP_UPLOAD_TOKEN', ''), os.environ.get('ERP_UPLOAD_USERNAME', ''), os.environ.get('ERP_UPLOAD_PASSWORD', ''))


def retry_pending(root, log):
    log('产品库上传已停用；待备份数据保留，不执行重试。')


def _archived_retry_pending(root, log):
    config = load_json(root / 'erp-backup-settings.json', {})
    outbox = BackupOutbox(root / 'erp-backup')
    for source in (outbox.root / 'sources').glob('*.json'):
        try:
            record = load_json(source, {})
            if record['target'] != config.get('base_url'):
                continue
            if 'inputs' in record:
                from erp_incremental import IncrementalOutbox
                IncrementalOutbox(outbox.root).prepare_row(record['ledger'], record['inputs'], record['target'], record['submitted'], record['definitions'])
            else:
                outbox.prepare(record['submitted'], record['ledger'], record['definitions'], record['locator'], record['target'])
        except Exception as error:
            log(f'产品库备份资料待修复 {source.stem}：{error}')
    for path in outbox.pending():
        client = client_from_config(config)
        try:
            client.upload(path, log)
        except Exception as error:
            log(f'产品库待备份 {path.stem}：{error}')
        finally:
            client.close()
