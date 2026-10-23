import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook, load_workbook
from core import load_json
from erp_incremental import IncrementalOutbox
from erp_ledger import ensure_row_ids, read_ledger, prepare_ledger

TARGET = 'http://localhost:8788'


class IncrementalTests(unittest.TestCase):
    def test_ab_identity_stable_across_batches_and_no_weak_merging(self):
        with tempfile.TemporaryDirectory() as folder:
            box = IncrementalOutbox(folder)
            a = {'shop_config_id': 'A', 'offer_id': 'same', 'title': 'Same title'}
            b = {**a, 'shop_config_id': 'B'}
            ia = box.identity(a, {'product_group_id': 'batch1', 'base_offer_id': 'base'}, TARGET)
            ib = box.identity(b, {'product_group_id': 'batch1', 'base_offer_id': 'base'}, TARGET)
            self.assertEqual(ia['product_id'], ib['product_id'])
            self.assertEqual(ia['variant_id'], ib['variant_id'])
            self.assertNotEqual(ia['listing_id'], ib['listing_id'])
            again = box.identity(a, {'product_group_id': 'batch2', 'base_offer_id': 'base'}, TARGET)
            self.assertEqual(again['product_id'], ia['product_id'])
            other = box.identity({**a, 'offer_id': 'different'}, {}, TARGET)
            self.assertNotEqual(other['product_id'], ia['product_id'])
            with self.assertRaises(ValueError):
                box.identity({'shop_config_id': 'C', 'offer_id': 'C'}, {'product_group_id': 'batch1', 'base_offer_id': 'different-spec'}, TARGET)

    def test_actual_reference_package_and_unknown_units(self):
        with tempfile.TemporaryDirectory() as folder:
            box = IncrementalOutbox(folder)
            row = dict(shop_config_id='A', offer_id='sku', gross_weight_g=355, depth_mm=200, width_mm=200, height_mm=200,
                       weight_unit='g', dimension_unit='mm', purchase_cost=0)
            path = box.prepare_row(row, {}, TARGET)
            doc = load_json(path, {})
            self.assertEqual(doc['schema_version'], '1.1.0')
            self.assertNotIn('revision', doc['products'][0])
            variant = doc['products'][0]['variants'][0]
            self.assertEqual(variant['package']['weight_g'], 355)
            self.assertEqual(variant['package']['dimensions_source'], 'unknown')
            self.assertEqual(variant['cost']['purchase'], '0.0000')
            self.assertEqual(path, box.prepare_row(row, {}, TARGET))
            row.pop('weight_unit')
            row.pop('dimension_unit')
            unknown = load_json(box.prepare_row(row, {}, TARGET), {})['products'][0]['variants'][0]['package']
            self.assertNotIn('weight_g', unknown)
            self.assertNotIn('length_mm', unknown)

    def test_hidden_ids_and_blank_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.xlsx'
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = '产品台账'
            sheet.append(['平台', '上品模式', '商品标题'])
            sheet.append(['Ozon', '跟卖', None])
            sheet.append(['WB', '跟卖', 'Other'])
            workbook.save(path)
            workbook.close()
            ensure_row_ids(path)
            first = list(read_ledger(path))
            ensure_row_ids(path)
            self.assertEqual(first, list(read_ledger(path)))
            report = prepare_ledger(first, folder, TARGET)
            self.assertEqual(report['items'][0]['status'], 'prepared')
            self.assertEqual(report['items'][1]['status'], 'failed')
            product = load_json(report['items'][0]['manifest_path'], {})['products'][0]
            self.assertIsNone(product['name'])

    def test_same_product_different_confirmed_variants(self):
        with tempfile.TemporaryDirectory() as folder:
            box = IncrementalOutbox(folder)
            a = box.identity({'shop_config_id': 'A', 'offer_id': 'small'}, {'backup_product_id': 'product', 'backup_variant_id': 'small'}, TARGET)
            b = box.identity({'shop_config_id': 'A', 'offer_id': 'big'}, {'backup_product_id': 'product', 'backup_variant_id': 'big'}, TARGET)
            self.assertEqual(a['product_id'], b['product_id'])
            self.assertNotEqual(a['variant_id'], b['variant_id'])
