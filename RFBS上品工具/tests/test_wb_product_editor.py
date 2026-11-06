import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from wb_product_editor import WbProductEditorMixin

class ProductEditorTests(unittest.TestCase):
    def app(self):
        app = WbProductEditorMixin()
        app._ui_call = lambda f: f()
        app._wb_task_log = Mock()
        app._wb_product_show = Mock()
        app._config = lambda: {'oss': {}}
        app.OUTPUT_DIR = Path('.')
        app.api = Mock(sandbox=False, content_url="https://example.test")
        app._wb_api_from_ui = lambda: app.api
        return app

    def test_duplicate_search_results_are_one_product_without_warehouses(self):
        app = self.app()
        app.wb_product_query = Mock()
        app.wb_product_query.get.return_value = 'abc,123,barcode'
        app.wb_product_tree = Mock()
        app.wb_product_tree.get_children.return_value = []
        app.api.cards_by_text.return_value = [{'nmID': 123, 'vendorCode': 'abc'}]
        app._wb_product_load()
        self.assertEqual(list(app.wb_product_cards), [123])
        app._wb_product_show.assert_called_once_with(123)
        app.api.warehouses.assert_not_called()
        app.api.get_stocks.assert_not_called()

    def test_three_warehouse_rows_do_not_repeat_image_submission(self):
        app = self.app()
        app.wb_inventory_rows = {str(i): {'nm': 123} for i in range(3)}
        card = {'nmID': 123, 'vendorCode': 'abc', 'photos': []}
        app.api.find_card.return_value = card
        app.api._request.return_value = {}
        with patch('wb_product_editor.atomic_write_json'), patch('wb_image_editor.resolve_sources', return_value=['https://cdn/image.jpg']):
            app._wb_product_submit_images({123: card}, {123: ['local.png']})
        app.api._request.assert_called_once()
        self.assertEqual(app.api._request.call_args.kwargs['json_body']['nmId'], 123)
        app.api.update_stock.assert_not_called()
