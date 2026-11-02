import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook
from pool_review import listed_source_ids


class ListedSourceTests(unittest.TestCase):
    def test_only_confirmed_ozon_rows_are_red_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.xlsx'
            book = Workbook()
            sheet = book.active
            sheet.title = '产品台账'
            sheet.append(['平台', 'Ozon来源链接', '状态'])
            sheet.append(['Ozon', 'https://www.ozon.ru/product/a-123456/', '上架及 RFBS 库存完成'])
            sheet.append(['Ozon', 'https://www.ozon.ru/product/987654/', '上架未完成'])
            sheet.append(['WB', 'https://www.ozon.ru/product/111111/', '上架及 RFBS 库存完成'])
            book.save(path)
            book.close()
            self.assertEqual(listed_source_ids(path), {'123456'})
