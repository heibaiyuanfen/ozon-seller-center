from pathlib import Path
import ast
p=Path('RFBS上品工具/app.py')
s=p.read_text(encoding='utf-8')
old="""        if link.startswith(('https://www.ozon.ru/', 'https://ozon.ru/')):
            ttk.Button(form, text='打开 Ozon 商品链接', command=lambda: webbrowser.open(link)).grid(row=1, column=1, sticky='e', padx=14)
"""
new="""        source_actions = ttk.Frame(form)
        source_actions.grid(row=1, column=1, sticky='e', padx=14)
        ttk.Button(source_actions, text='双图对照', command=lambda: self._show_pool_source_comparison(item, window)).pack(side=tk.LEFT, padx=(0, 8))
        if link.startswith(('https://www.ozon.ru/', 'https://ozon.ru/')):
            ttk.Button(source_actions, text='打开 Ozon 商品链接', command=lambda: webbrowser.open(link)).pack(side=tk.LEFT)
"""
assert old in s
s=s.replace(old,new,1)
s=s.replace("        ttk.Button(buttons, text='双图对照 / 1688链接', command=lambda: self._show_pool_source_comparison(item, window)).pack(side=tk.LEFT, padx=5)\n",'',1)
ast.parse(s)
p.write_text(s,encoding='utf-8')
