import json
import unittest
from unittest.mock import Mock, patch

from app import RfbsListingApp
from core import ReferenceProduct
from services import CopywritingService


class SourceCopyTests(unittest.TestCase):
    def test_tags_only_response_preserves_source_copy_and_line_breaks(self):
        session = Mock()
        session.post.return_value.json.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "title_ru": "Must not replace source",
                "description_ru": "Must not replace source description",
                "tags_ru": [f"освещение дома {i}" for i in range(25)],
            })}}]
        }
        source = ReferenceProduct("https://ozon.ru/product/1", "Лампа LED", "Описание.\nВторая строка.")
        result = CopywritingService("https://example.test/v1", "key", "model", session=session).generate(
            source, reuse_source_copy=True,
        )
        self.assertEqual(result["title_ru"], source.title)
        self.assertEqual(result["description_ru"], source.description)

        session.post.return_value.json.return_value = {"tags_ru": [f"освещение дома {i}" for i in range(25)]}
        result = CopywritingService("https://example.test/v1", "key", "model", session=session).generate(
            source, reuse_source_copy=True,
        )
        self.assertEqual(result["title_ru"], source.title)
        self.assertGreaterEqual(len(result["tags_ru"]), 25)
        prompt = session.post.call_args.kwargs["json"]["messages"][1]["content"]
        self.assertIn("Do not rewrite or return", prompt)
        self.assertNotIn("title_ru (string", prompt)
        session.post.return_value.json.return_value = {"choices": [{"message": {"content": json.dumps({
            "tags_ru": [f"освещение дома {i}" for i in range(25)],
        })}}]}
        result = CopywritingService("https://example.test/v1", "key", "model", session=session).generate(
            source, reuse_source_copy=True,
        )
        self.assertEqual(result["description_ru"], source.description)

    def test_missing_source_copy_stops_before_ai_request(self):
        session = Mock()
        service = CopywritingService("https://example.test/v1", "key", "model", session=session)
        for title, description in (("Лампа", ""), ("", "Описание")):
            with self.subTest(title=title), self.assertRaisesRegex(ValueError, "来源标题或描述为空"):
                service.generate(ReferenceProduct("", title, description), reuse_source_copy=True)
        session.post.assert_not_called()

    def test_follow_reuses_source_but_local_listing_keeps_generation(self):
        for local_mode in (False, True):
            with self.subTest(local_mode=local_mode), patch("app.CopywritingService") as service_class:
                app = RfbsListingApp.__new__(RfbsListingApp)
                app.reference = ReferenceProduct("", "Лампа", "Описание")
                app._active_listing_is_local = Mock(return_value=local_mode)
                app._ensure_vision_analysis = Mock(return_value={})
                app._reference_from_local_vision = Mock(return_value=app.reference)
                app._config = Mock(return_value={"text": {"api_url": "https://example.test", "api_key": "key", "model": "model"}})
                service_class.return_value.api_url = "https://example.test"
                service_class.return_value.generate.return_value = {
                    "title_ru": "Лампа", "description_ru": "Описание", "tags_ru": [f"освещение дома {i}" for i in range(25)],
                }
                app.root = Mock()
                app._log = Mock()
                app._generate_copywriting()
                service_class.return_value.generate.assert_called_once_with(
                    app.reference, reuse_source_copy=not local_mode,
                )
                if not local_mode:
                    app._ensure_vision_analysis.assert_not_called()


if __name__ == "__main__":
    unittest.main()

