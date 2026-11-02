import unittest
from decimal import Decimal
from core import calculate_roi_price
from pool_review import DEFAULT_RULES, evaluate_product


class PoolAutoPricingTests(unittest.TestCase):
    def test_price_matches_workbench_and_review_is_independent(self):
        item = dict(purchase_cost='100', weight='300', target_roi='30', sales_commission_percent='12',
                    label_fee='2', advertising_percent='0', cargo_loss_percent='3')
        result = evaluate_product(item, DEFAULT_RULES, exchange_rate='12.5')
        expected = calculate_roi_price('100', '2', '30', Decimal('.3'), sales_commission_percent='12',
                                       exchange_rate='12.5', advertising_percent='0', cargo_loss_percent='3')
        self.assertEqual(Decimal(result['price']), expected.price)
        self.assertEqual(result['status'], '待审核')
        item.update(supplier_confirmed='1', economics_confirmed='1', review_confirmed_at='2026-11-02T00:00:00')
        self.assertEqual(evaluate_product(item, DEFAULT_RULES)['status'], '已审核')

    def test_missing_cost_cannot_produce_price(self):
        result = evaluate_product({}, DEFAULT_RULES)
        self.assertEqual(result['price'], '')
        self.assertTrue(result['reason'])
