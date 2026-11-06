import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from wb_ui import WbUiMixin
from wb import WildberriesApi

class VariantTests(unittest.TestCase):
    def test_manual_price_skips_calculator(self):
        inputs = dict(pricing_mode="manual", price="80", manual_currency="CNY", purchase_cost="", sales_commission_percent="")
        with patch.object(WbUiMixin, "_wb_calculate_pricing", side_effect=AssertionError("must not calculate")):
            WbUiMixin._wb_apply_listing_price(inputs)
        self.assertEqual(inputs["price"], "80")
        self.assertEqual(inputs["currency_code"], "CNY")

    def test_invalid_manual_prices(self):
        for price in ("", "0", "-1", "1.5", "NaN", "Infinity"):
            with self.subTest(price=price), self.assertRaises(ValueError):
                WbUiMixin._wb_apply_listing_price(dict(pricing_mode="manual",price=price))

    def test_overseas_waits_for_formula(self):
        with self.assertRaisesRegex(ValueError, "公式"):
            WbUiMixin._wb_apply_listing_price(dict(pricing_mode="overseas_warehouse",price="80"))

    def test_cross_border_uses_computed_price(self):
        from decimal import Decimal
        result=Mock(list_price=Decimal("123"),currency_code="CNY")
        result.json_values.return_value={"calculated":True}
        inputs=dict(pricing_mode="cross_border",price="80")
        with patch.object(WbUiMixin,"_wb_calculate_pricing",return_value=result):
            WbUiMixin._wb_apply_listing_price(inputs)
        self.assertEqual(inputs["price"],"123")

    def test_correction_preserves_identity_and_replaces_rejected_values(self):
        inputs=dict(subject_id="1",vendor_code="v1",title="Title",description="Description",
                    barcode="123456",length_cm="10",width_cm="10",height_cm="10",weight_kg="1",
                    characteristic_definitions=[dict(charcID=1,name="颜色",charcType=1,required=True)],
                    characteristic_values={"1":["invalid"]})
        state=dict(inputs=inputs,stage="new",status="failed",error="invalid color",task_id="task1",payload=["old"])
        corrected=WbUiMixin._wb_correct_task(state,{"characteristic_values":{"1":["желтый"]}})
        self.assertEqual(corrected["inputs"]["barcode"],"123456")
        self.assertEqual(corrected["inputs"]["vendor_code"],"v1")
        self.assertEqual(corrected["task_id"],"task1")
        self.assertEqual(corrected["status"],"queued")
        self.assertEqual(state["inputs"]["characteristic_values"],{"1":["invalid"]})
        self.assertNotIn("payload",corrected)
        self.assertEqual(corrected["previous_error"],"invalid color")
        with self.assertRaises(ValueError):
            WbUiMixin._wb_correct_task(state,{"characteristic_values":{}})

    def test_correction_rejects_inflight_and_completed(self):
        for stage in ("card_submitted","card_ready","price_ready","completed"):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                WbUiMixin._wb_correct_task(dict(stage=stage),{})

    def test_merge_request(self):
        api = WildberriesApi('test')
        api._request = Mock(return_value={})
        api.merge_cards(31, [7, 9])
        self.assertEqual(api._request.call_args.kwargs['json_body'], {'targetIMT':31,'nmIDs':[7,9]})

    def test_independent_images_prices_stock_and_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            host = WbUiMixin()
            host.OUTPUT_DIR = Path(folder)
            host._ui_call = lambda f:f()
            host._log = lambda msg:None
            host._wb_connection_settings = lambda:dict(token='test',sandbox=False)
            host._wb_collect_form_snapshot = lambda:dict(pricing_mode='manual',manual_currency='CNY',vendor_code='parent',subject_id='1',subject_display='test',warehouse_id='88',warehouse_display='WH')
            host.wb_warehouse_by_display={'WH':{'id':88}}
            host.wb_variant_mode=Mock();host.wb_variant_mode.get.return_value='group'
            host.wb_variant_rows=[dict(id=str(i),vendor_code=f'v{i}',tech_size=str(i),wb_size='',style='yellow',price=str(100+i),stock=str(10+i),image_paths=[f'{i}.jpg']) for i in (1,2)]
            host._analysis_config=lambda:{}
            host._wb_validate_listing_inputs=lambda *a,**k:None
            host._wb_copy_stable_images=lambda paths,code:list(paths)
            host.PRODUCT_LEDGER_PATH=Path(folder)/'ledger'
            host._wb_product_ledger_record=lambda state:{}
            api=Mock()
            api.subject_characteristics.return_value=[]
            api.find_card.side_effect=[{'imtID':5},{'imtID':6},{'imtID':5},{'imtID':5}]
            processed=[]
            def advance(state):
                processed.append(copy.deepcopy(state))
                state.update(stage='completed',nm_id=len(processed))
                return state
            with patch('wb_ui.WildberriesApi',return_value=api), patch('wb_ui.WbCardAiService'), patch('wb_ui.build_card_content_document',return_value={}), patch('wb_ui.parse_card_content_document',return_value=dict(title='title',description='desc',characteristic_values={})), patch('wb_ui.WbListingWorkflow') as workflow, patch('wb_ui.upsert_product_ledger'),patch('wb_ui.messagebox.showinfo'):
                workflow.return_value.advance.side_effect=advance
                host._wb_run_variants_locked()
            self.assertEqual([x['image_paths'] for x in processed],[['1.jpg'],['2.jpg']])
            self.assertEqual([x['inputs']['price'] for x in processed],['101','102'])
            self.assertEqual([x['inputs']['stock'] for x in processed],['11','12'])
            self.assertTrue(all(x['inputs']['warehouse_id']=='88' for x in processed))
            api.merge_cards.assert_called_once_with(5,[2])

if __name__=='__main__':unittest.main()
