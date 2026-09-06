"""Hidden Tk integration tests; every write uses an isolated temporary root."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from mercadolibre.models import Picture, ProductDraft
from mercadolibre.ui import MercadoLibrePanel


class MercadoLibreUITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mercadolibre-ui-test-")
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "mercadolibre"
        self.network_patch = patch(
            "requests.sessions.Session.request", side_effect=AssertionError("Unexpected network request"),
        )
        self.network = self.network_patch.start()
        self.addCleanup(self.network_patch.stop)
        self.modal_patch = patch(
            "mercadolibre.ui.messagebox.showerror", side_effect=AssertionError("Unexpected modal error"),
        )
        self.modal_patch.start()
        self.addCleanup(self.modal_patch.stop)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tcl/Tk unavailable for hidden UI checks: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.panel = MercadoLibrePanel(self.root, self.data_dir)
        self.panel.pack(fill="both", expand=True)
        self.root.update_idletasks()

    def tearDown(self):
        self.network.assert_not_called()

    def complete_draft(self):
        draft = ProductDraft(
            title="Local lamp reference", family_name="Adjustable desk lamp",
            description="A desk lamp with an adjustable metal arm.",
            seller_sku="UI-LAMP-1", category_id="CBT123", price="29.90",
            available_quantity="5", pictures=[Picture(picture_id="123-MLA987_012026")],
        )
        self.panel._display(draft)
        return draft

    def test_panel_imports_and_builds_without_ozon_or_wb_modules(self):
        script = textwrap.dedent("""
            import builtins
            import pathlib
            import sys
            import tkinter as tk
            from unittest.mock import patch

            sys.path.insert(0, sys.argv[1])
            original_import = builtins.__import__
            forbidden = {'app', 'core', 'services', 'wb', 'wb_ui', 'runtime_paths', 'amazon_source', 'seerfar_import'}
            def isolated_import(name, globals=None, locals=None, fromlist=(), level=0):
                if level == 0 and name.split('.')[0] in forbidden:
                    raise AssertionError('Unexpected shared application import: ' + name)
                return original_import(name, globals, locals, fromlist, level)
            builtins.__import__ = isolated_import
            from mercadolibre.ui import MercadoLibrePanel
            root = tk.Tk()
            root.withdraw()
            try:
                with patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected HTTP')) as request:
                    panel = MercadoLibrePanel(root, pathlib.Path(sys.argv[2]))
                    panel.pack()
                    root.update_idletasks()
                    assert len(panel.tabs.tabs()) == 6
                    assert panel.publish_button.instate(['disabled'])
                    assert not forbidden.intersection(sys.modules)
                    request.assert_not_called()
            finally:
                root.destroy()
        """)
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(APP_DIR), str(self.data_dir / "standalone")],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_opening_current_selected_draft_preserves_and_saves_form_edits(self):
        draft = self.complete_draft()
        self.panel._save()
        self.panel.draft_tree.selection_set(draft.id)
        self.panel.form["family_name"].set("Edited family name")
        self.panel.form["supplier_sku"].set("PROCUREMENT-EDIT")
        self.panel._open_draft()
        self.assertEqual(self.panel.current.id, draft.id)
        self.assertEqual(self.panel.form["family_name"].get(), "Edited family name")
        saved = self.panel.store.load_draft(draft.id)
        self.assertEqual(saved.family_name, "Edited family name")
        self.assertEqual(saved.procurement.supplier_sku, "PROCUREMENT-EDIT")

    def test_new_and_switch_save_each_product_without_cross_contamination(self):
        first = self.complete_draft()
        self.panel.form["supplier_sku"].set("SUPPLIER-A")
        self.panel._new_draft()
        self.assertNotEqual(self.panel.current.id, first.id)
        self.assertEqual(self.panel.store.load_draft(first.id).procurement.supplier_sku, "SUPPLIER-A")
        second_id = self.panel.current.id
        self.panel.form["family_name"].set("Second product family")
        self.panel.form["seller_sku"].set("UI-LAMP-2")
        self.panel.form["supplier_sku"].set("SUPPLIER-B")
        self.panel._save()
        self.panel.form["price"].set("44.90")
        self.panel.draft_tree.selection_set(first.id)
        self.panel._open_draft()
        self.assertEqual(self.panel.current.id, first.id)
        self.assertEqual(self.panel.form["supplier_sku"].get(), "SUPPLIER-A")
        second = self.panel.store.load_draft(second_id)
        self.assertEqual(second.price, "44.90")
        self.assertEqual(second.procurement.supplier_sku, "SUPPLIER-B")
        self.assertEqual(self.panel.store.load_draft(first.id).price, "29.90")
        self.assertEqual(len(self.panel.store.list_drafts()), 2)

    def test_local_prepare_persists_candidate_and_keeps_real_publish_disabled(self):
        draft = self.complete_draft()
        self.panel._prepare()
        paths = list((self.data_dir / "products" / draft.id / "plans").glob("*.json"))
        self.assertEqual(len(paths), 1)
        record = json.loads(paths[0].read_text(encoding="utf-8"))
        plan = record["plan"]
        self.assertEqual(plan["state"], "prepared")
        self.assertIsInstance(plan["payload"], list)
        self.assertEqual(plan["payload"][0]["family_name"], draft.family_name)
        self.assertFalse(plan["publish_enabled"])
        self.assertFalse(plan["api_validated"])
        self.assertTrue(self.panel.publish_button.instate(["disabled"]))
        self.assertEqual(self.panel.tabs.select(), str(self.panel.preview_tab))
        self.assertIn(str(paths[0]), self.panel.status.get())
        self.assertIn("候选请求已生成（未发布）", self.panel.status.get())
        self.assertIn('"publish_enabled": false', self.panel.preview.get("1.0", "end-1c"))

    def test_incomplete_form_prepares_missing_fields_report_without_candidate(self):
        draft_id = self.panel.current.id
        self.panel._prepare()
        paths = list((self.data_dir / "products" / draft_id / "plans").glob("*.json"))
        self.assertEqual(len(paths), 1)
        plan = json.loads(paths[0].read_text(encoding="utf-8"))["plan"]
        self.assertEqual(plan["state"], "invalid")
        self.assertIsNone(plan["payload"])
        self.assertIn("需补充", self.panel.preview.get("1.0", "end-1c"))
        self.assertTrue(self.panel.publish_button.instate(["disabled"]))

    def test_settings_and_drafts_never_persist_session_token(self):
        self.complete_draft()
        marker = "SESSION-TOKEN-NOT-FOR-DISK-987654"
        self.panel.token.set(marker)
        self.panel.form["account_label"].set("Separate Mercado Libre account")
        self.panel.form["app_id"].set("123456789")
        self.panel.form["redirect_uri"].set("https://seller.example/callback")
        self.panel._save_settings()
        self.panel._prepare()
        files = list(self.data_dir.rglob("*.json"))
        self.assertGreaterEqual(len(files), 3)
        for path in files:
            with self.subTest(path=path.name):
                content = path.read_text(encoding="utf-8")
                self.assertNotIn(marker, content)
                self.assertNotIn('"access_token"', content)
        self.panel.destroy()
        self.panel = MercadoLibrePanel(self.root, self.data_dir)
        self.root.update_idletasks()
        self.assertEqual(self.panel.token.get(), "")
        self.assertEqual(self.panel.form["account_label"].get(), "Separate Mercado Libre account")
        self.assertEqual(self.panel.form["app_id"].get(), "123456789")
        self.assertEqual(len(self.panel.draft_tree.get_children()), 1)


if __name__ == "__main__":
    unittest.main()
