import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from wb import WildberriesApi
from wb_inventory import WbInventoryMixin

class InventoryTests(unittest.TestCase):
    def test_get_stock_keeps_zero_and_unknown_separate(self):
        api=WildberriesApi('test');api._request=Mock(return_value={'stocks':[{'chrtId':3,'amount':0}]})
        values=api.get_stocks(7,[3,4])
        self.assertEqual(values,{3:0})
        self.assertEqual(api._request.call_args.kwargs['json_body'],{'chrtIds':[3,4]})

    def test_write_zero(self):
        api=WildberriesApi('test');api._request=Mock()
        api.update_stock(7,3,0)
        self.assertEqual(api._request.call_args.kwargs['json_body'],{'stocks':[{'chrtId':3,'amount':0}]})
        for invalid in (-1,1.5,True,''):
            with self.assertRaises(ValueError):api.update_stock(7,3,invalid)

    def test_warehouses_and_readback(self):
        host=WbInventoryMixin();api=Mock(sandbox=False)
        api.get_stocks.side_effect=[{3:0},{3:80}]
        host._wb_api_from_ui=lambda:api;host._ui_call=lambda f:f();host._wb_task_log=lambda m:None
        host._wb_inventory_show=lambda key:None;host._wb_inventory_record_override=Mock()
        rows={'7:3':dict(vendor='v',warehouse='A',warehouse_id=7,chrt=3,current=5,target=0),
              '8:3':dict(vendor='v',warehouse='B',warehouse_id=8,chrt=3,current=5,target=80)}
        host.wb_inventory_rows=copy.deepcopy(rows)
        host._wb_inventory_submit(rows)
        self.assertEqual([c.args for c in api.update_stock.call_args_list],[(7,3,0),(8,3,80)])
        self.assertTrue(all(r['target']=='' for r in host.wb_inventory_rows.values()))

    def test_readback_mismatch_not_success(self):
        host=WbInventoryMixin();api=Mock(sandbox=False);api.get_stocks.return_value={}
        host._wb_api_from_ui=lambda:api;host._ui_call=lambda f:f();host._wb_task_log=lambda m:None
        host._wb_inventory_show=lambda key:None;host._wb_inventory_record_override=Mock()
        row=dict(vendor='v',warehouse='A',warehouse_id=7,chrt=3,current=5,target=0)
        host.wb_inventory_rows={'7:3':row}
        host._wb_inventory_submit({'7:3':copy.deepcopy(row)})
        self.assertEqual(row['target'],0)
        self.assertEqual(row['current'],'未返回')
        self.assertNotEqual(row['status'],'已读回确认')

    def test_color_update_preserves_sizes_and_documents(self):
        card=dict(nmID=1,vendorCode='v',title='title',description='desc',brand='brand',dimensions={'length':10},
                  sizes=[{'chrtID':3,'techSize':'S','skus':['abc']}],characteristics=[{'id':1,'value':['old']},{'id':2,'value':7}],
                  documents={'items':[{'id':'doc'}]},photos=[{'big':'url'}])
        result=WildberriesApi.color_update_payload(card,1,'бежевый')
        self.assertEqual(result['sizes'],card['sizes']);self.assertEqual(result['documents'],card['documents'])
        self.assertEqual(result['characteristics'],[{'id':2,'value':7},{'id':1,'value':['бежевый']}])
        self.assertNotIn('photos',result)
        self.assertEqual(card['characteristics'][0]['value'],['old'])

    def test_zero_override_prevents_old_resume_quantity(self):
        import json
        host=WbInventoryMixin()
        with tempfile.TemporaryDirectory() as folder:
            host.OUTPUT_DIR=Path(folder)
            path=host.OUTPUT_DIR/'wb-group-job.json'
            path.write_text(json.dumps([{'chrt_id':3,'inputs':{'warehouse_id':'7','stock':'1000'}}]),encoding='utf-8')
            host._wb_inventory_record_override(dict(warehouse_id=7,chrt=3,target=0))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))[0]['inputs']['stock'],'0')
