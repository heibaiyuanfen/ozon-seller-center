import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from mercadolibre.models import (
    AccountSnapshot, Attribute, JobState, ListingRoute, MarketplaceTarget,
    Picture, Procurement, ProductDraft, Variant,
)
from mercadolibre.workflow import prepare_listing, publish


class MercadoLibreWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.draft = ProductDraft(
            title="Private source title", family_name="Adjustable desk lamp",
            description="Adjustable desk lamp with a metal base.",
            seller_sku="LAMP-1", category_id="CBT123", price="24.90",
            available_quantity="8", account_id="100",
            pictures=[Picture(picture_id="123-MLA987_012026")],
            attributes=[Attribute(id="BRAND", value_id="1234", value_name="Example")],
            procurement=Procurement(
                supplier_url="https://detail.1688.com/offer/private.html",
                supplier_sku="PRIVATE-COST-SKU", cost_cny="19.90", notes="private procurement note",
            ),
        )
        self.account = AccountSnapshot(
            user_id="100", site_id="CBT", tags=["user_product_seller"], checked_at="2026-09-06",
            marketplaces=[{"user_id": 200, "site_id": "MLM", "logistic_type": "remote"}],
        )
        self.category = {"id": "CBT123", "settings": {"listing_allowed": True, "max_pictures_per_item": 3}}
        self.definitions = [{
            "id": "BRAND", "name": "Brand", "tags": {"required": True},
            "values": [{"id": "1234", "name": "Example"}],
        }]

    def prepare(self):
        return prepare_listing(
            self.draft, account=self.account, category=self.category,
            attribute_definitions=self.definitions,
        )

    def assert_invalid(self, plan, field):
        self.assertEqual(plan.state, JobState.INVALID.value)
        self.assertIsNone(plan.payload)
        self.assertTrue(any(issue.field == field and issue.severity == "error" for issue in plan.issues))
        self.assertFalse(plan.publish_enabled)

    def variants(self):
        return [
            Variant(seller_sku="LAMP-BLACK", price="24.90", available_quantity="2",
                    picture_ids=["black-image-id"], attribute_combinations=[Attribute(id="COLOR", value_name="Black")]),
            Variant(seller_sku="LAMP-WHITE", price="26.50", available_quantity="3",
                    picture_ids=["white-image-id"], attribute_combinations=[Attribute(id="COLOR", value_name="White")]),
        ]

    def test_default_route_and_single_product_use_canonical_family_array(self):
        self.assertEqual(ProductDraft().route, ListingRoute.USER_PRODUCTS.value)
        plan = self.prepare()
        self.assertEqual(plan.state, JobState.PREPARED.value)
        self.assertFalse(plan.publish_enabled)
        self.assertFalse(plan.api_validated)
        self.assertIsInstance(plan.payload, list)
        self.assertEqual(len(plan.payload), 1)
        product = plan.payload[0]
        self.assertEqual(product["family_name"], "Adjustable desk lamp")
        self.assertEqual(product["price"], 24.9)
        self.assertEqual(product["currency_id"], "USD")
        self.assertEqual(product["available_quantity"], 8)
        self.assertEqual(product["description"], {"plain_text": self.draft.description})
        self.assertEqual(product["pictures"], [{"id": "123-MLA987_012026"}])
        self.assertEqual(product["sites_to_sell"], [{"site_id": "MLM", "logistic_type": "remote"}])
        attributes = {attribute["id"]: attribute for attribute in product["attributes"]}
        self.assertEqual(attributes["SELLER_SKU"]["value_name"], "LAMP-1")
        self.assertEqual(attributes["ITEM_CONDITION"]["value_id"], "2230284")
        submission = next(step for step in plan.steps if step.get("method") == "POST")
        self.assertEqual(submission["path"], "/global/user-products/families")
        self.assertFalse(submission["implemented"])

    def test_payload_contains_no_legacy_fields_or_procurement_details(self):
        payload = self.prepare().payload
        serialized = json.dumps(payload)
        for forbidden in ("Private source title", "PRIVATE-COST-SKU", "private procurement note",
                          "1688.com", '"title"', '"variations"', '"procurement"',
                          '"supplier_url"', '"cost_cny"', '"ozon"', '"warehouse_id"'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

    def test_multi_variant_emits_independent_family_products(self):
        self.draft.variations = self.variants()
        plan = self.prepare()
        self.assertEqual(plan.state, JobState.PREPARED.value)
        self.assertEqual(len(plan.payload), 2)
        self.assertEqual([item["price"] for item in plan.payload], [24.9, 26.5])
        self.assertEqual([item["available_quantity"] for item in plan.payload], [2, 3])
        for product, variant in zip(plan.payload, self.draft.variations):
            attributes = {attribute["id"]: attribute for attribute in product["attributes"]}
            self.assertEqual(attributes["SELLER_SKU"]["value_name"], variant.seller_sku)
            self.assertEqual(attributes["COLOR"]["value_name"], variant.attribute_combinations[0].value_name)
            self.assertEqual(attributes["BRAND"]["value_id"], "1234")
            self.assertEqual(product["pictures"], [{"id": variant.picture_ids[0]}])
            self.assertNotIn("variations", product)
            self.assertNotIn("title", product)

    def test_each_variant_must_supply_required_attribute_after_inheritance(self):
        self.draft.variations = self.variants()
        self.definitions.append({"id": "COLOR", "tags": {"new_required": True}})
        self.assertEqual(self.prepare().state, JobState.PREPARED.value)
        self.draft.variations[1].attribute_combinations = []
        self.assert_invalid(self.prepare(), "attributes")

    def test_required_enum_attribute_presence_checked_but_enum_business_validation_is_deferred(self):
        self.draft.attributes = []
        self.assert_invalid(self.prepare(), "attributes")
        self.draft.attributes = [Attribute(id="BRAND", value_id="not-in-cached-enum")]
        plan = self.prepare()
        self.assertEqual(plan.state, JobState.PREPARED.value)
        self.assertFalse(plan.api_validated)
        self.assertTrue(any(issue.field == "schema" and issue.severity == "warning" for issue in plan.issues))

    def test_duplicate_skus_and_trimmed_duplicate_skus_are_rejected(self):
        for second_sku in ("LAMP-BLACK", " LAMP-BLACK "):
            self.draft.variations = self.variants()
            self.draft.variations[1].seller_sku = second_sku
            with self.subTest(second_sku=second_sku):
                self.assert_invalid(self.prepare(), "seller_sku")

    def test_nonfinite_nonpositive_and_overprecision_prices_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e1000", "0", "-1", "1.123", "not-a-price"):
            self.draft.price = value
            with self.subTest(value=value):
                self.assert_invalid(self.prepare(), "variations")

    def test_net_proceeds_mode_has_only_selected_amount_key(self):
        self.draft.price_mode = "global_net_proceeds"
        product = self.prepare().payload[0]
        self.assertEqual(product["global_net_proceeds"], 24.9)
        self.assertNotIn("price", product)

    def test_target_must_match_account_site_and_logistics_pair(self):
        self.draft.targets.append(MarketplaceTarget(site_id="MLB", logistic_type="remote"))
        self.assert_invalid(self.prepare(), "targets")
        self.draft.targets = [MarketplaceTarget(site_id="MLM", logistic_type="fulfillment")]
        self.assert_invalid(self.prepare(), "targets")

    def test_wrong_account_or_category_cache_cannot_prepare(self):
        self.draft.account_id = "different-account"
        self.assert_invalid(self.prepare(), "account_id")
        self.draft.account_id = self.account.user_id
        self.category["id"] = "CBT999"
        self.assert_invalid(self.prepare(), "category_id")

    def test_duplicate_target_and_target_price_override_are_rejected(self):
        self.draft.targets.append(MarketplaceTarget())
        self.assert_invalid(self.prepare(), "targets")
        self.draft.targets = [MarketplaceTarget(price="29.90")]
        self.assert_invalid(self.prepare(), "targets")

    def test_account_detects_both_up_tags_and_keeps_managed_and_local_routes_separate(self):
        for tag in ("user_product_seller", "user_products_seller"):
            self.account.tags = [tag]
            with self.subTest(tag=tag):
                self.assertEqual(self.account.route, ListingRoute.USER_PRODUCTS)
                self.assertEqual(self.prepare().state, JobState.PREPARED.value)
        self.account.marketplaces[0]["business_model"] = "CBT CN Fulfillment Managed"
        self.assertEqual(self.account.route, ListingRoute.FULLY_MANAGED)
        self.assert_invalid(self.prepare(), "route")
        self.account.site_id = "MLM"
        self.assertEqual(self.account.route, ListingRoute.LOCAL)
        self.assert_invalid(self.prepare(), "route")

    def test_mixed_account_cannot_send_managed_target_through_standard_route(self):
        self.account.marketplaces.append({
            "site_id": "MLB", "logistic_type": "fulfillment",
            "business_model": "CBT CN Fulfillment Managed",
        })
        self.assertEqual(self.account.route, ListingRoute.USER_PRODUCTS)
        self.draft.targets = [MarketplaceTarget(site_id="MLB", logistic_type="fulfillment")]
        self.assert_invalid(self.prepare(), "targets")

    def test_local_or_url_pictures_require_uploaded_platform_ids(self):
        for picture in (Picture(local_path="D:/private/lamp.jpg"), Picture(source="https://example.com/lamp.jpg")):
            self.draft.pictures = [picture]
            with self.subTest(picture=picture):
                self.assert_invalid(self.prepare(), "pictures")

    def test_variant_picture_limits_and_duplicate_ids_are_checked(self):
        self.draft.variations = self.variants()
        self.draft.variations[0].picture_ids = ["image-a", "image-a"]
        self.assert_invalid(self.prepare(), "pictures")
        self.draft.variations[0].picture_ids = ["image-a", "image-b", "image-c", "image-d"]
        self.assert_invalid(self.prepare(), "pictures")

    def test_planning_and_disabled_publish_never_make_network_requests(self):
        with patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected HTTP")) as request:
            plan = self.prepare()
            self.assertEqual(plan.state, JobState.PREPARED.value)
            with self.assertRaisesRegex(RuntimeError, "尚未接通真实发布"):
                publish(plan, access_token="not-a-real-token")
            request.assert_not_called()

    def test_prepare_and_json_roundtrip_leave_inputs_unchanged(self):
        self.draft.variations = self.variants()
        original = copy.deepcopy(self.draft.to_dict())
        original_account = copy.deepcopy(self.account)
        original_category = copy.deepcopy(self.category)
        original_definitions = copy.deepcopy(self.definitions)
        restored = ProductDraft.from_dict(json.loads(json.dumps(original)))
        self.assertEqual(restored.to_dict(), original)
        self.prepare()
        self.assertEqual(self.draft.to_dict(), original)
        self.assertEqual(self.account, original_account)
        self.assertEqual(self.category, original_category)
        self.assertEqual(self.definitions, original_definitions)
        self.assertEqual(restored.to_dict(), original)

    def test_import_rejects_foreign_fields_and_malformed_nested_models(self):
        cases = [
            {"ozon_client_id": "private"}, {"schema_version": 2}, {"price": 24.9},
            {"targets": {}}, {"pictures": [{"source": 123}]},
            {"variations": [{"picture_ids": [123]}]},
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                ProductDraft.from_dict(data)


if __name__ == "__main__":
    unittest.main()
