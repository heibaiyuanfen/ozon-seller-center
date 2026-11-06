import unittest
from unittest.mock import patch
from wb_image_editor import resolve_sources

class ImageSourcesTests(unittest.TestCase):
    def test_remote_preserved(self):
        with patch('services.OssService.upload') as upload:
            self.assertEqual(resolve_sources(['https://a/image.jpg'], {}, '1'), ['https://a/image.jpg'])
            upload.assert_not_called()

    def test_local_uploaded_once_and_order_preserved(self):
        with patch('wb_image_editor.Image.open'), patch('services.OssService.upload', return_value=['https://cdn/new.jpg']) as upload:
            result = resolve_sources(['local.png', 'https://a/old.jpg', 'local.png'], {}, '1')
            self.assertEqual(result, ['https://cdn/new.jpg', 'https://a/old.jpg', 'https://cdn/new.jpg'])
            self.assertEqual(upload.call_args.args[0], ['local.png'])

    def test_incomplete_upload_rejected(self):
        with patch('wb_image_editor.Image.open'), patch('services.OssService.upload', return_value=[]):
            with self.assertRaises(ValueError): resolve_sources(['local.png'], {}, '1')

    def test_upload_failure_propagates(self):
        with patch('wb_image_editor.Image.open'), patch('services.OssService.upload', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError): resolve_sources(['local.png'], {}, '1')
