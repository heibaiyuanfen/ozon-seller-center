import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from seerfar_import import merge_product_mappings

class EnrichedMappingTests(unittest.TestCase):
    def test_new_cost_and_supplier_replace_stale_approval(self):
        product = dict(id='1', enriched_source='1', purchase_cost='8.5', supplier_1688_url='https://detail.1688.com/offer/123.html', notes='核对规格')
        item = merge_product_mappings([product], {'1': dict(purchase_cost='3', supplier_confirmed='1', economics_confirmed='1')})[0]
        self.assertEqual(item['purchase_cost'], '8.5')
        self.assertEqual(item['supplier_1688_url'], product['supplier_1688_url'])
        self.assertEqual(item['supplier_confirmed'], '0')
        self.assertEqual(item['economics_confirmed'], '0')

    def test_missing_new_cost_does_not_reuse_old_cost(self):
        item = merge_product_mappings([dict(id='1', enriched_source='1', purchase_cost='')], {'1': dict(purchase_cost='3')})[0]
        self.assertEqual(item['purchase_cost'], '')
