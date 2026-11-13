import unittest
from pool_category import resolve_pool_category
class CategoryTests(unittest.TestCase):
    def setUp(self):
        self.categories = {'家居 / 杯子': {'name': '杯子', 'display_ru': 'Дом / Кружки'}, '服装 / 上衣': {'name': '上衣'}}
    def test_mixed_products(self):
        self.assertEqual(resolve_pool_category({'category':'家居 > 杯子'},self.categories,'服装 / 上衣'),'家居 / 杯子')
        self.assertEqual(resolve_pool_category({'category':'上衣'},self.categories),'服装 / 上衣')
    def test_russian(self):
        self.assertEqual(resolve_pool_category({'category':'Дом / Кружки'},self.categories),'家居 / 杯子')
    def test_missing_uses_explicit_pool_default(self):
        self.assertEqual(resolve_pool_category({},self.categories,'默认'),'默认')
    def test_unknown_never_uses_pool(self):
        with self.assertRaises(ValueError): resolve_pool_category({'category':'未知'},self.categories,'默认')
    def test_ambiguous_rejected(self):
        self.categories['其他 / 杯子']={'name':'杯子'}
        with self.assertRaises(ValueError): resolve_pool_category({'category':'杯子'},self.categories)

    def test_bilingual_newlines(self):
        catalog = {'电子产品 / 电子清洁刷': {'name': '电子清洁刷', 'name_ru': 'Щетка для чистки электроники'}}
        for separator in ('\n', '\r\n'):
            item = {'category': '电子清洁刷' + separator + 'Щетка для чистки электроники'}
            self.assertEqual(resolve_pool_category(item, catalog), '电子产品 / 电子清洁刷')

    def test_russian_alias_resolves_unknown_chinese_translation(self):
        catalog = {'电子产品 / 清洁刷': {'name': '清洁刷', 'name_ru': 'Щетка для чистки электроники'}}
        self.assertEqual(resolve_pool_category({'category': '电子清洁刷\nЩетка для чистки электроники'}, catalog), '电子产品 / 清洁刷')

    def test_conflicting_bilingual_aliases_rejected(self):
        with self.assertRaises(ValueError):
            resolve_pool_category({'category': '杯子\n上衣'}, self.categories)

    def test_bilingual_disambiguates_duplicate_name(self):
        catalog = {'甲 / 刷子': {'name': '刷子', 'name_ru': 'A'}, '乙 / 刷子': {'name': '刷子', 'name_ru': 'B'}}
        self.assertEqual(resolve_pool_category({'category': '刷子\nB'}, catalog), '乙 / 刷子')
