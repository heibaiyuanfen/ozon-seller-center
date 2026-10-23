"""Manual GUI smoke check with isolated app data, no network or publication."""
import os
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory() as directory:
        os.environ["OZON_RFBS_DATA_DIR"] = directory
        import tkinter as tk
        from tkinter import ttk
        from app import RfbsListingApp
        from test_pool_review import product
        root = tk.Tk()
        root.withdraw()
        app = RfbsListingApp(root)
        app.product_pool_products = [product(), product(id="987654321", sku="987654321", purchase_cost="800")]
        app._refresh_product_pool_tree()
        assert len(app.product_pool_tree.get_children()) == 2
        app.pool_review_filter_var.set("通过")
        app._refresh_product_pool_tree()
        assert len(app.product_pool_tree.get_children()) == 1
        app.product_pool_tree.selection_set("0")
        app._edit_selected_pool_product()
        root.update_idletasks()
        for window in root.winfo_children():
            if isinstance(window, tk.Toplevel):
                window.withdraw()
                window.destroy()
        app._edit_pool_rules()
        root.update_idletasks()
        for window in root.winfo_children():
            if isinstance(window, tk.Toplevel):
                window.withdraw()
                window.destroy()
        app._review_pool_selection()
        root.update_idletasks()
        review_windows = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert review_windows
        trees = [w for w in review_windows[0].winfo_children() if isinstance(w, ttk.Treeview)]
        assert len(trees[0].get_children()) == 1
        app._save_product_pool()
        assert (Path(directory) / "选品池.json").is_file()
        app.prefetch_executor.shutdown(wait=False, cancel_futures=True)
        app._closing = True
        root.destroy()
        print("GUI smoke OK: rules, scroll editor, filters, review and isolated persistence")


if __name__ == "__main__":
    main()
