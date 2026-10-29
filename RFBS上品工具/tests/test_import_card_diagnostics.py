import unittest
from unittest.mock import Mock
from services import OzonApi


class ImportCardTests(unittest.TestCase):
    def test_imported_card_with_diagnostics_is_not_creation_failure(self):
        api = OzonApi.__new__(OzonApi)
        api.post = Mock()
        result = {'result': {'items': [{'offer_id': 'A', 'status': 'imported',
                                      'errors': [{'code': 'MARKING'}]}]}}
        self.assertEqual(api.confirm_created_product('A', result)['offer_id'], 'A')
        api.post.assert_not_called()

    def test_failed_import_can_have_existing_exact_card(self):
        api = OzonApi.__new__(OzonApi)
        api.post = Mock(return_value={'items': [{'offer_id': 'A', 'id': 123}]})
        self.assertEqual(api.confirm_created_product('A', {})['id'], 123)
        api.post.assert_called_once_with('/v3/product/info/list', {'offer_id': ['A']})

    def test_other_offer_or_missing_identity_is_not_confirmation(self):
        for items in ([{'offer_id': 'B', 'id': 123}], [{'offer_id': 'A', 'id': 0}], []):
            api = OzonApi.__new__(OzonApi)
            api.post = Mock(return_value={'items': items})
            with self.assertRaises(RuntimeError):
                api.confirm_created_product('A', {})

    def test_processing_diagnostics_do_not_end_polling(self):
        api = OzonApi.__new__(OzonApi)
        api.import_info = Mock(side_effect=[
            {'result': {'items': [{'status': 'processing', 'errors': [{'code': 'MARKING'}]}]}},
            {'result': {'items': [{'status': 'imported'}]}}])
        result = api.wait_import(1, attempts=2, interval=0)
        self.assertEqual(result['result']['items'][0]['status'], 'imported')
