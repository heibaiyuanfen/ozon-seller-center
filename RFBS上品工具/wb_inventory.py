from __future__ import annotations
import copy
import re
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from core import atomic_write_json, load_json
from wb import WildberriesApi


class WbInventoryMixin:
    def _build_wb_inventory_tab(self):
        tab = ttk.Frame(self.wb_product_tabs, padding=8)
        self.wb_product_tabs.add(tab, text="6. 库存管理")
        self.wb_inventory_rows = {}
        self.wb_inventory_colors = {}
        self.wb_inventory_query = tk.StringVar()
        toolbar = ttk.Frame(tab);toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="货号 / WB ID / 条码（多个用逗号分隔）").pack(side=tk.LEFT)
        ttk.Entry(toolbar, textvariable=self.wb_inventory_query, width=45).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(toolbar, text="读取商品及全部仓库库存", command=lambda:self._run(self._wb_inventory_load)).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="读取当前变体", command=self._wb_inventory_use_variants).pack(side=tk.LEFT)
        ttk.Label(tab, text="每行代表一个商品尺码在一个仓库的库存。双击设置目标数量；0 表示清零，留空表示不修改。先读取，再提交目标数量。",
                  wraplength=1100).pack(anchor="w", pady=6)
        frame = ttk.Frame(tab);frame.pack(fill=tk.BOTH, expand=True)
        columns=("vendor", "nm", "size", "barcode", "warehouse", "current", "target", "color", "status")
        self.wb_inventory_tree=ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for key,title,width in zip(columns,("货号","WB ID","尺码","条码","仓库","实际库存","目标库存","颜色","结果"),(140,100,70,140,160,80,80,140,240)):
            self.wb_inventory_tree.heading(key,text=title);self.wb_inventory_tree.column(key,width=width)
        self.wb_inventory_tree.pack(side=tk.LEFT,fill=tk.BOTH,expand=True)
        scroll=ttk.Scrollbar(frame,command=self.wb_inventory_tree.yview);scroll.pack(side=tk.RIGHT,fill=tk.Y)
        self.wb_inventory_tree.configure(yscrollcommand=scroll.set)
        horizontal=ttk.Scrollbar(tab,orient="horizontal",command=self.wb_inventory_tree.xview)
        horizontal.pack(fill=tk.X)
        self.wb_inventory_tree.configure(xscrollcommand=horizontal.set)
        self.wb_inventory_tree.bind("<Double-1>",lambda _e:self._wb_inventory_set_target())
        actions=ttk.Frame(tab);actions.pack(fill=tk.X,pady=6)
        ttk.Button(actions,text="设置选中行库存（可为 0）",command=self._wb_inventory_set_target).pack(side=tk.LEFT)
        ttk.Button(actions,text="取消选中行修改",command=self._wb_inventory_clear_target).pack(side=tk.LEFT)
        ttk.Button(actions,text="提交已填写的目标库存",command=self._wb_inventory_confirm).pack(side=tk.LEFT)
        colors=ttk.Frame(tab);colors.pack(fill=tk.X)
        ttk.Button(colors,text="读取官方颜色",command=lambda:self._run(self._wb_inventory_load_colors)).pack(side=tk.LEFT)
        self.wb_uniform_color=self._var("wb_uniform_color", "")
        self.wb_inventory_color_combo=ttk.Combobox(colors,textvariable=self.wb_uniform_color,state="readonly",width=30)
        self.wb_inventory_color_combo.pack(side=tk.LEFT,padx=6)
        ttk.Button(colors,text="统一选中商品颜色",command=self._wb_inventory_confirm_color).pack(side=tk.LEFT)
        ttk.Label(colors,text="选定的官方颜色也用于后续新变体 AI 资料；修改已上架商品需点击统一颜色。").pack(side=tk.LEFT)

    def _wb_inventory_use_variants(self):
        self.wb_inventory_query.set(",".join(row["vendor_code"] for row in self.wb_variant_rows))
        self._run(self._wb_inventory_load)

    def _wb_inventory_show(self, key):
        row=self.wb_inventory_rows[key]
        values=(row['vendor'],row['nm'],row['size'],row['barcode'],row['warehouse'],row.get('current','未返回'),row.get('target',''),row.get('color',''),row.get('status',''))
        if self.wb_inventory_tree.exists(key):self.wb_inventory_tree.item(key,values=values)
        else:self.wb_inventory_tree.insert('',tk.END,iid=key,values=values)

    def _wb_inventory_load(self):
        query=self._ui_call(self.wb_inventory_query.get).strip()
        if not query:raise ValueError("请输入商品货号、WB ID 或条码")
        if self._ui_call(lambda:any(row.get('target','') != '' for row in self.wb_inventory_rows.values())):
            raise ValueError("有未提交的库存修改，请先提交或取消后再重新读取")
        api=self._wb_api_from_ui()
        if api.sandbox:raise ValueError("请切换到正式环境读取商品库存")
        self._wb_task_log("读取商品及仓库库存")
        cards={}
        for term in filter(None,re.split(r'[,，;；\n]+',query)):
            for card in api.cards_by_text(term.strip()):cards[int(card['nmID'])]=card
        if not cards:raise ValueError("未找到商品，请检查货号或 WB ID")
        warehouses=api.warehouses()
        if not warehouses:raise ValueError("当前店铺没有返回卖家仓库")
        rows={}
        for warehouse in warehouses:
            wid=int(warehouse['id'])
            sizes=[int(size['chrtID']) for card in cards.values() for size in card.get('sizes',[]) if size.get('chrtID')]
            try:
                stocks=api.get_stocks(wid,sizes)
                error=''
            except Exception as exc:stocks={};error=str(exc)
            for card in cards.values():
                color=next((x.get('value') for x in card.get('characteristics',[]) if any(w in str(x.get('name','')).lower() for w in ('颜色','цвет','color'))),'')
                for size in card.get('sizes',[]):
                    cid=int(size['chrtID']);key=f'{wid}:{cid}'
                    rows[key]=dict(vendor=card['vendorCode'],nm=int(card['nmID']),chrt=cid,size=size.get('techSize',''),barcode=','.join(size.get('skus',[])),warehouse=f"{warehouse.get('name','')} [{wid}]",warehouse_id=wid,current=stocks.get(cid,'未返回'),target='',color=str(color),status=error or ('已读取' if cid in stocks else '未返回库存，不能当作 0'))
        def apply():
            self.wb_inventory_rows=rows
            self.wb_inventory_tree.delete(*self.wb_inventory_tree.get_children())
            for key in rows:self._wb_inventory_show(key)
        self._ui_call(apply)
        self._wb_task_log(f"已读取 {len(cards)} 个商品、{len(warehouses)} 个仓库，共 {len(rows)} 条尺码库存")

    def _wb_inventory_set_target(self):
        if getattr(self,'busy',False):return
        selected=self.wb_inventory_tree.selection()
        if not selected:return
        value=simpledialog.askinteger("设置目标库存",f"为选中的 {len(selected)} 行设置库存数量（0 表示清零）",parent=self.root,minvalue=0)
        if value is None:return
        for key in selected:
            self.wb_inventory_rows[key]['target']=value
            self.wb_inventory_rows[key]['status']='待提交'
            self._wb_inventory_show(key)

    def _wb_inventory_clear_target(self):
        if getattr(self,'busy',False):return
        for key in self.wb_inventory_tree.selection():
            self.wb_inventory_rows[key]['target']=''
            self._wb_inventory_show(key)

    def _wb_inventory_confirm(self):
        rows={key:copy.deepcopy(row) for key,row in self.wb_inventory_rows.items() if row.get('target','') != ''}
        if not rows:
            messagebox.showinfo("库存","请先设置目标库存");return
        summary='\n'.join(f"{r['vendor']} / {r['warehouse']} / {r['size']}：{r['current']} → {r['target']}" for r in rows.values())
        if messagebox.askyesno("确认库存修改",summary):self._run(lambda:self._wb_inventory_submit(rows))

    def _wb_inventory_submit(self, rows):
        api=self._wb_api_from_ui()
        if api.sandbox:raise ValueError("Sandbox 不写真实库存")
        failures=[]
        for key,row in rows.items():
            self._wb_task_log(f"设置 {row['vendor']} / {row['warehouse']} 库存为 {row['target']}")
            current=row['current']
            try:
                api.update_stock(row['warehouse_id'],row['chrt'],row['target'])
                self._wb_inventory_record_override(row)
                current=api.get_stocks(row['warehouse_id'],[row['chrt']]).get(row['chrt'],'未返回')
                matched=current==row['target']
                status='已读回确认' if matched else '已提交，读回尚未匹配；请稍后读取核对'
            except Exception as exc:
                matched=False;status=str(exc);failures.append(row['vendor']+': '+status)
            def apply(key=key,current=current,status=status,matched=matched):
                live=self.wb_inventory_rows[key];live.update(current=current,status=status)
                if matched:live['target']=''
                self._wb_inventory_show(key)
            self._ui_call(apply)
            self._wb_task_log(status)
        if failures:raise RuntimeError('部分库存未完成：'+'；'.join(failures))

    def _wb_inventory_record_override(self,row):
        # Keep an unfinished listing from restoring its old quantity on resume.
        for name in ('wb-group-job.json','wb-sizes-job.json'):
            path=self.OUTPUT_DIR/name
            batch=load_json(path,[]);changed=False
            for state in batch:
                inputs=state.get('inputs',{})
                if int(inputs.get('warehouse_id') or 0)!=row['warehouse_id']:continue
                if state.get('size_variants'):
                    for size in state['size_variants']:
                        if int(size.get('chrt_id') or 0)==row['chrt']:
                            size['stock']=str(row['target']);changed=True
                            for original in inputs.get('size_variants',[]):
                                if original.get('barcode')==size.get('barcode'):original['stock']=str(row['target'])
                elif int(state.get('chrt_id') or 0)==row['chrt']:
                    inputs['stock']=str(row['target']);changed=True
            if changed:atomic_write_json(path,batch)
        single=getattr(self,'wb_job',{})
        if single and int(single.get('chrt_id') or 0)==row['chrt'] and int(single.get('inputs',{}).get('warehouse_id') or 0)==row['warehouse_id']:
            single['inputs']['stock']=str(row['target']);self._save_wb_job(single)

    def _wb_inventory_load_colors(self):
        values=self._wb_api_from_ui().directory_values('colors',locale='ru')
        choices=[]
        for raw in values:
            value=raw.get('name') or raw.get('value') if isinstance(raw,dict) else raw
            if isinstance(value,str):choices.append(value)
        def apply():
            self.wb_inventory_colors={value:value for value in choices}
            self.wb_inventory_color_combo.configure(values=choices)
        self._ui_call(apply)
        self._wb_task_log(f"已读取 {len(choices)} 个官方颜色")

    def _wb_inventory_confirm_color(self):
        color=self.wb_uniform_color.get()
        if color not in self.wb_inventory_colors:
            messagebox.showwarning("颜色","请先读取并选择官方颜色");return
        vendors=list(dict.fromkeys(self.wb_inventory_rows[key]['vendor'] for key in self.wb_inventory_tree.selection()))
        if not vendors:return
        if messagebox.askyesno("统一颜色",f"将 {', '.join(vendors)} 的颜色统一为 {color}，继续吗？"):
            self._run(lambda:self._wb_inventory_update_colors(vendors,color))

    def _wb_inventory_update_colors(self,vendors,color):
        api=self._wb_api_from_ui()
        failures=[]
        for vendor in vendors:
            try:
                card=api.find_card(vendor)
                if not card:raise ValueError('商品未找到')
                definitions=api.subject_characteristics(int(card['subjectID']))
                color_id=next((d['charcID'] for d in definitions if any(w in d.get('name','').lower() for w in ('颜色','цвет','color'))),None)
                if not color_id:raise ValueError('该类目没有颜色属性')
                payload=WildberriesApi.color_update_payload(card,color_id,color)
                atomic_write_json(self.OUTPUT_DIR/f"wb-color-backup-{card['nmID']}-{time.time_ns()}.json",card)
                api.update_card(payload)
                for name in ('wb-group-job.json','wb-sizes-job.json'):
                    path=self.OUTPUT_DIR/name
                    batch=load_json(path,[]);changed=False
                    for state in batch:
                        if state.get('inputs',{}).get('vendor_code')==vendor:
                            state['inputs'].setdefault('characteristic_values',{})[str(color_id)]=[color];changed=True
                    if changed:atomic_write_json(path,batch)
                updated=api.find_card(vendor)
                actual=next((x.get('value') for x in (updated or {}).get('characteristics',[]) if int(x.get('id',0))==int(color_id)),None)
                status='颜色已确认' if actual==[color] or actual==color else '颜色修改已提交，等待同步后重新读取'
                def apply(vendor=vendor,actual=actual,status=status):
                    for key,row in self.wb_inventory_rows.items():
                        if row['vendor']==vendor:
                            row.update(color=str(actual),status=status);self._wb_inventory_show(key)
                self._ui_call(apply)
                self._wb_task_log(vendor+'：'+status)
            except Exception as exc:
                failures.append(vendor+': '+str(exc));self._wb_task_log(failures[-1])
        if failures:raise RuntimeError('部分颜色修改失败：'+'；'.join(failures))

