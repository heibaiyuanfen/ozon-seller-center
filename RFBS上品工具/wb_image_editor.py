"""Local/remote gallery drafts; upload local files only when submitting."""
import copy
import io
import queue
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageOps, ImageTk
import requests


def resolve_sources(sources, config, product):
    from services import OssService
    local = list(dict.fromkeys(s for s in sources if not s.startswith(('http://', 'https://'))))
    for path in local:
        with Image.open(path) as image:
            image.verify()
    urls = OssService(config).upload(local, f'wb-{product}-{time.time_ns()}') if local else []
    if len(urls) != len(local) or any(not u.startswith(('http://', 'https://')) for u in urls):
        raise ValueError('图床未返回完整有效链接，未修改商品图库')
    mapping = dict(zip(local, urls))
    return [mapping.get(s, s) for s in sources]


def open_image_editor(app, cards):
    dialog = tk.Toplevel(app.root)
    dialog.title('产品图片预览与修改')
    dialog.geometry('1000x680')
    ids = list(cards)
    drafts = {n: [p.get('big') or p.get('c516x688') for p in c.get('photos', [])] for n, c in cards.items()}
    if any(not u for values in drafts.values() for u in values):
        dialog.destroy()
        messagebox.showerror('图片', '原图地址缺失，请重新读取')
        return
    original = copy.deepcopy(drafts)
    choice = ttk.Combobox(dialog, state='readonly', values=[f'{cards[n]["vendorCode"]} [{n}]' for n in ids])
    choice.pack(fill=tk.X, padx=8, pady=8)
    choice.current(0)
    ttk.Label(dialog, text='第 1 张为主图。点击图片序号查看预览；本地图片在提交时上传到设置中的阿里云 OSS 图床。').pack()
    body = ttk.Frame(dialog)
    body.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
    listing = tk.Listbox(body, width=48, selectmode=tk.EXTENDED, exportselection=False)
    listing.pack(side=tk.LEFT, fill=tk.Y)
    preview = ttk.Label(body, text='选择图片预览', anchor='center')
    preview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    status = ttk.Label(dialog, text='')
    status.pack(fill=tk.X)
    results = queue.Queue()
    generation = [0]
    cache = {}
    def current(): return ids[choice.current()]
    def show(*_):
        generation[0] += 1
        token = generation[0]
        selected = listing.curselection()
        preview.configure(image='', text='加载预览…' if selected else '选择图片预览')
        preview.image = None
        if not selected: return
        source = drafts[current()][selected[0]]
        def load():
            try:
                if source in cache:
                    thumb = cache[source]
                else:
                    if source.startswith(('http://', 'https://')):
                        with requests.get(source, timeout=20) as response:
                            response.raise_for_status()
                            image = Image.open(io.BytesIO(response.content))
                    else: image = Image.open(source)
                    with image:
                        thumb = ImageOps.exif_transpose(image).convert('RGB')
                        thumb.thumbnail((510, 470))
                    cache[source] = thumb
                results.put((token, thumb, None))
            except Exception as error: results.put((token, None, str(error)))
        threading.Thread(target=load, daemon=True).start()
    def poll():
        if not dialog.winfo_exists(): return
        while not results.empty():
            token, thumb, error = results.get()
            if token != generation[0]: continue
            if error: preview.configure(image='', text='预览失败：' + error)
            else:
                photo = ImageTk.PhotoImage(thumb, master=dialog)
                preview.configure(image=photo, text='')
                preview.image = photo
        dialog.after(100, poll)
    def refresh(*_):
        listing.delete(0, tk.END)
        for i, source in enumerate(drafts[current()], 1):
            local = not source.startswith(('http://', 'https://'))
            listing.insert(tk.END, f'{i}. {"本地待上传" if local else "线上图片"} — {Path(source).name}')
        status.configure(text=f'当前商品 {len(drafts[current()])} 张图片；共选中 {len(ids)} 个商品')
        if drafts[current()]: listing.selection_set(0)
        show()
    batch = tk.BooleanVar(value=False)
    ttk.Checkbutton(dialog, text='批量操作所有选中商品（替换和删除按相同图片序号）', variable=batch).pack(anchor='w', padx=8)
    def edit(action):
        indexes = list(listing.curselection())
        if action != 'add' and not indexes: return
        paths = []
        if action != 'delete':
            paths = list(filedialog.askopenfilenames(parent=dialog, title='选择本地图片', filetypes=[('图片', '*.jpg *.jpeg *.png *.webp *.bmp *.gif')]))
            if not paths: return
            if action == 'replace' and len(paths) != 1:
                messagebox.showerror('替换', '请选择一张图片替换选中位置', parent=dialog); return
            try:
                for path in paths:
                    with Image.open(path) as image: image.verify()
            except Exception as error:
                messagebox.showerror('图片无法读取', str(error), parent=dialog); return
        updated = copy.deepcopy(drafts)
        for nm in ids if batch.get() else [current()]:
            values = updated[nm]
            if action != 'add' and any(i >= len(values) for i in indexes):
                messagebox.showerror('序号不存在', f'{cards[nm]["vendorCode"]} 没有所选序号，本次操作未应用', parent=dialog); return
            if action == 'add': values.extend(paths)
            elif action == 'replace':
                for i in indexes: values[i] = paths[0]
            else:
                for i in reversed(indexes): values.pop(i)
            if not 1 <= len(values) <= 30:
                messagebox.showerror('图片数量', '每个商品需保留 1–30 张图片', parent=dialog); return
        drafts.update(updated)
        refresh()
    controls = ttk.Frame(dialog)
    controls.pack(fill=tk.X, padx=8, pady=8)
    for title, action in [('本地添加图片', 'add'), ('本地替换选中图片', 'replace'), ('删除选中图片', 'delete')]:
        ttk.Button(controls, text=title, command=lambda a=action: edit(a)).pack(side=tk.LEFT, padx=4)
    def submit():
        if getattr(app, 'busy', False): return
        changes = {n: list(v) for n, v in drafts.items() if v != original[n]}
        if not changes: return
        summary = '\n'.join(f'{cards[n]["vendorCode"]}：{len(original[n])} → {len(v)} 张' for n, v in changes.items())
        if messagebox.askyesno('上传图床并修改商品图片', summary + '\n本地图片上传成功后，将提交线上图库修改。', parent=dialog):
            snapshot = copy.deepcopy(changes)
            def work():
                app._wb_product_submit_images(cards, snapshot)
                app._ui_call(lambda: dialog.destroy() if dialog.winfo_exists() else None)
            app._run(work)
    ttk.Button(controls, text='上传图床并提交修改', command=submit).pack(side=tk.RIGHT)
    choice.bind('<<ComboboxSelected>>', refresh)
    listing.bind('<<ListboxSelect>>', show)
    refresh()
    poll()
