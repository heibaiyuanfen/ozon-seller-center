import unittest
from store_collection import product_links, product_links_from_html


class StoreCollectionTests(unittest.TestCase):
    def test_html_import_only_product_links(self):
        html = '<a href="/product/item-123456/?x=1">item</a><a href="https://evil.example/product/999999/">bad</a>'
        self.assertEqual(product_links_from_html(html), ['https://www.ozon.ru/product/123456/'])
    def test_dedup_and_filter(self):
        links = ['/product/item-123456/?x=1', '/product/123456/',
                 'https://evil.example/product/987654/', '/seller/example/',
                 'https://ozon.ru/product/other-987654/']
        self.assertEqual(product_links(links), ['https://www.ozon.ru/product/123456/',
                                                'https://www.ozon.ru/product/987654/'])
