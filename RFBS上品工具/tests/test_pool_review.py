import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

from core import calculate_price_breakdown
from app import RfbsListingApp
from pool_review import DEFAULT_RULES, evaluate_product, purchase_ceiling, pricing_values, validate_rules
from seerfar_import import merge_product_mappings, mapping_payload


def product(**overrides):
    return {"id": "123456789", "sku": "123456789", "listing_url": "https://www.ozon.ru/product/123456789/",
        "category": "家居 / 灯具", "market_price": "1000", "sales": "120", "sales_period_days": "30",
        "purchase_cost": "100", "sales_commission_percent": "20", "weight": "300", "net_weight": "250",
        "depth": "100", "width": "100", "height": "100", "stock": "1", "model_name": "LAMP",
        "supplier_1688_url": "https://detail.1688.com/offer/123.html", "supplier_sku": "白色 / 单件",
        "supplier_confirmed": "1", "economics_confirmed": "1", "review_confirmed_at": datetime.now().isoformat(),
        "offer_id": "OFFER-1", "old_price": "0", **overrides}


class PoolReviewTests(unittest.TestCase):
    def test_review_uses_configured_exchange_for_tariff_and_reverse_budget(self):
        item = product(market_price="120", purchase_cost="1", weight="520")
        rules = {**DEFAULT_RULES, "min_roi": "0", "min_margin": "0"}
        low = evaluate_product(item, rules, exchange_rate="12.5")
        high = evaluate_product(item, rules, exchange_rate="15")
        self.assertNotEqual(low["ceiling"], high["ceiling"])
        expected = calculate_price_breakdown("120", "1", "2", ".52", "20", exchange_rate="15")
        self.assertEqual(high["profit"], f"{expected.profit:.2f}")
        self.assertEqual(evaluate_product(item, rules, exchange_rate="0")["status"], "待补充")

    def test_passing_review_uses_market_price_not_old_target_listing_price(self):
        result = evaluate_product(product(price="9999"), DEFAULT_RULES)
        self.assertEqual(result["status"], "通过")
        self.assertEqual(result["price"], "1000")
        self.assertGreater(Decimal(result["ceiling"]), 100)

    def test_ceiling_rounds_down_and_obeys_both_margin_and_roi(self):
        for fbp in (False, True):
            for roi, margin in (("60", "25"), ("500", "5")):
                rules = {**DEFAULT_RULES, "min_roi": roi, "min_margin": margin}
                values = pricing_values(product(), rules, fbp)
                ceiling = purchase_ceiling("1000", "300", values, margin, fbp)
                def check(cost):
                    b = calculate_price_breakdown("1000", cost, values["label_fee"], ".3", "20",
                        sales_commission_discount_percent="1" if fbp else "0")
                    return b.roi_percent >= Decimal(roi) and b.profit / 1000 * 100 >= Decimal(margin)
                self.assertTrue(check(ceiling))
                self.assertFalse(check(ceiling + Decimal(".01")))

    def test_review_price_override_and_unprofitable_product(self):
        result = evaluate_product(product(review_price="200", price="9999"), DEFAULT_RULES)
        self.assertEqual(result["price"], "200")
        self.assertEqual(result["status"], "淘汰")
        self.assertIn("利润未达标", result["reason"])

    def test_missing_data_never_defaults_to_zero_or_pass(self):
        for field in ("market_price", "weight", "purchase_cost", "sales_commission_percent", "sales_period_days", "supplier_sku", "model_name", "net_weight", "depth", "review_confirmed_at"):
            with self.subTest(field=field):
                result = evaluate_product(product(**{field: ""}), DEFAULT_RULES)
                self.assertNotEqual(result["status"], "通过")
                self.assertTrue(result["reason"])

    def test_supplier_and_economics_require_explicit_confirmation(self):
        for field in ("supplier_confirmed", "economics_confirmed"):
            result = evaluate_product(product(**{field: "0"}), DEFAULT_RULES)
            self.assertEqual(result["status"], "待补充")

    def test_period_not_scaled_and_sales_rule_rejects(self):
        self.assertEqual(evaluate_product(product(sales_period_days="7"), DEFAULT_RULES)["status"], "待补充")
        self.assertEqual(evaluate_product(product(sales="99"), DEFAULT_RULES)["status"], "淘汰")
        self.assertEqual(evaluate_product(product(sales="0", sales_period_days=""), {**DEFAULT_RULES, "min_sales": ""})["status"], "通过")

    def test_expired_or_future_confirmations_block(self):
        for days in (-8, 1):
            stamp = (datetime.now() + timedelta(days=days)).isoformat()
            self.assertEqual(evaluate_product(product(review_confirmed_at=stamp), DEFAULT_RULES)["status"], "待补充")

    def test_rule_filters_and_validation(self):
        for change in ({"category_keyword": "服装"}, {"min_price": "1100"}, {"max_price": "900"}, {"max_weight": "200"}):
            self.assertEqual(evaluate_product(product(), {**DEFAULT_RULES, **change})["status"], "淘汰")
        for change in ({"min_roi": "NaN"}, {"min_margin": "100"}, {"sales_period_days": "1.5"}, {"min_price": "2", "max_price": "1"}):
            with self.assertRaises(ValueError):
                validate_rules(change)

    def test_mapping_round_trip_preserves_review_inputs_not_imported_gross_margin(self):
        item = product(review_price="950", gross_margin="99", label_fee="3")
        restored = merge_product_mappings([{"id": item["id"], "market_price": "1000"}], mapping_payload([item]))[0]
        for key in ("supplier_sku", "supplier_confirmed", "review_confirmed_at", "review_price", "sales_period_days", "label_fee"):
            self.assertEqual(restored[key], item[key])
        self.assertEqual(evaluate_product(product(purchase_cost="700", gross_margin="99"), DEFAULT_RULES)["status"], "淘汰")


class PoolQueueTests(unittest.TestCase):
    def app(self):
        app = RfbsListingApp.__new__(RfbsListingApp)
        app.auto_jobs = {}
        app.PRODUCT_LEDGER_PATH = None
        app.product_pool_rules = dict(DEFAULT_RULES)
        app.ozon_shops = {"shop": {"name": "Test shop", "client_id": "1", "api_key": "test"}}
        app.multi_shop_targets = {}
        app.category_by_display = {"灯具": {"description_category_id": 1, "type_id": 2, "display": "灯具"}}
        app._job_inputs_from_form = Mock(return_value={"ozon_shop_id": "shop", "ozon_shop_name": "Test shop",
            "category_display": "灯具", "category_id": "1", "type_id": "2", "warehouse_id": "1",
            "image_mode": "original", "fbp_pricing": "0", "stock": "999", "model_name": "WRONG-INHERITED"})
        app._new_offer_id = Mock(return_value="AUTO-1")
        return app

    def test_queue_preserves_reviewed_price_and_product_facts(self):
        app = self.app()
        jobs = app._prepare_reviewed_pool_jobs([product(review_price="950")])
        self.assertEqual(len(jobs), 1)
        inputs = jobs[0]["inputs"]
        self.assertEqual(inputs["price"], "950")
        self.assertEqual(inputs["model_name"], "LAMP")
        self.assertEqual(inputs["stock"], "1")
        self.assertEqual(inputs["image_mode"], "original")
        self.assertEqual(inputs["pool_review"]["status"], "通过")
        self.assertEqual(app.auto_jobs, {})  # preview never publishes or commits

    def test_block_same_source_or_offer_and_missing_product_facts(self):
        app = self.app()
        app.auto_jobs = {"old": {"inputs": {"ozon_shop_id": "shop", "source_url": "123456789"}}}
        with self.assertRaisesRegex(ValueError, "已有队列记录"):
            app._prepare_reviewed_pool_jobs([product()])
        app.auto_jobs = {}
        with self.assertRaisesRegex(ValueError, "重复"):
            app._prepare_reviewed_pool_jobs([product(), product()])
        for fields in ({"stock": ""}, {"weight": ""}, {"model_name": ""}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                app._prepare_reviewed_pool_jobs([product(**fields)])

    def test_recomputes_after_cost_changes_instead_of_trusting_review_result(self):
        with self.assertRaises(ValueError):
            self.app()._prepare_reviewed_pool_jobs([product(purchase_cost="800", review_result={"status": "通过"})])

    def test_multi_shop_and_fbp(self):
        app = self.app()
        app.ozon_shops["shop2"] = {"name": "Second", "client_id": "2", "api_key": "test"}
        app.multi_shop_targets = {"shop": {"warehouse_id": "1"}, "shop2": {"warehouse_id": "2"}}
        app._job_inputs_from_form.return_value["fbp_pricing"] = "1"
        jobs = app._prepare_reviewed_pool_jobs([product()])
        self.assertEqual(len(jobs), 2)
        self.assertNotEqual(jobs[0]["inputs"]["offer_id"], jobs[1]["inputs"]["offer_id"])
        self.assertTrue(all(j["inputs"]["label_fee"] == "0" for j in jobs))
        self.assertEqual({j["inputs"]["warehouse_id"] for j in jobs}, {"1", "2"})


if __name__ == "__main__":
    unittest.main()
