import unittest
from decimal import Decimal

from core import calculate_shipping, calculate_roi_price, calculate_price_breakdown
from app import RfbsListingApp, JOB_INPUT_KEYS, WORKSPACE_FIELD_KEYS


class ShippingExchangeTests(unittest.TestCase):
    def test_spreadsheet_examples(self):
        for price, weight, expected in ((116, '.32', '12.36'), (120, '.52', '35.76'), (140, '1.3', '54.50')):
            self.assertEqual(calculate_shipping(price, weight, '12.5'), Decimal(expected))

    def test_inclusive_value_and_weight_limits(self):
        for price, weight, expected in ((120, '.5', '17.42'), (560, '2', '74.17'),
                                        (20000, '5', '165.21'), (120, '30', '598.83'),
                                        (560, '30', '883.44'), (20000, '30', '843.64'),
                                        ('120.01', '.5', '32.02'), ('560.01', '2', '80.91')):
            self.assertEqual(calculate_shipping(price, weight), Decimal(expected))
        self.assertEqual(calculate_shipping('.08', '.1'), Decimal('6.18'))
        for price, weight in (('.079', '.1'), ('20000.01', '.1'), ('120', '30.01')):
            with self.assertRaises(ValueError):
                calculate_shipping(price, weight)

    def test_exchange_validation_and_effect(self):
        self.assertEqual(calculate_shipping('120', '.52', '13'), Decimal('32.58'))
        for rate in ('', '0', '-1', 'NaN', 'Infinity', 'bad'):
            with self.assertRaises(ValueError):
                calculate_shipping('120', '.52', rate)

    def test_minimum_roi_matches_exhaustive_search_across_bands(self):
        for rate in ('10', '12.5', '15'):
            for weight in ('.32', '.52', '2', '5', '5.01', '30'):
                result = calculate_roi_price('50', '2', '60', weight,
                    sales_commission_percent='14', exchange_rate=rate)
                checked = calculate_price_breakdown(result.price, '50', '2', weight, '14', exchange_rate=rate)
                self.assertEqual(result.shipping, checked.shipping)
                self.assertGreaterEqual(checked.roi_percent, 60)
                for price in range(1, int(result.price)):
                    previous = calculate_price_breakdown(price, '50', '2', weight, '14', exchange_rate=rate)
                    self.assertLess(previous.roi_percent, 60)

    def test_job_uses_saved_exchange(self):
        self.assertIn('shipping_exchange_rate', JOB_INPUT_KEYS)
        self.assertIn('shipping_exchange_rate', WORKSPACE_FIELD_KEYS)
        values = dict(purchase_cost='50', label_fee='2', target_roi='60', weight='520', sales_commission_percent='14', shipping_exchange_rate='15')
        result = RfbsListingApp._job_price_breakdown(values)
        expected = calculate_roi_price('50', '2', '60', '.52', sales_commission_percent='14', exchange_rate='15')
        self.assertEqual(result, expected)
