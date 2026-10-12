# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
import sys
import subprocess

from PyInstaller.utils.hooks import collect_all


tool_dir = Path(SPEC).resolve().parent

datas = []
binaries = []
hiddenimports = []
hiddenimports += ["parallel_worker", "parallel_runtime", "tls_runtime", "shared_source"]
for package_name in ("playwright", "alibabacloud_oss_v2", "openpyxl", "certifi"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# PyInstaller may reject the bundled Python's Tk probe on managed runtimes;
# include Tk explicitly so the GUI import and native extension are packaged.
tk_root = Path(sys.base_prefix)
datas += [(str(tk_root / "Lib" / "tkinter"), "tkinter")]
hiddenimports += ["tkinter", "_tkinter"]
binaries += [(str(tk_root / "DLLs" / "_tkinter.pyd"), ".")]

# Python 3.13 + PyInstaller may discover Tcl/Tk subdirectories while omitting
# the root scripts (notably init.tcl and tk.tcl). Include both runtime trees
# explicitly so the packaged Tkinter GUI can initialize on a clean machine.
tcl_root = Path(sys.base_prefix) / "tcl"
datas += [
    (str(tcl_root / "tcl8.6"), "_tcl_data"),
    (str(tcl_root / "tk8.6"), "_tk_data"),
]

# Include a browser runtime so scraping works on a clean Windows computer.
playwright_root = Path(
    os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    or (Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
)
edge_root = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application"
edge_versions = sorted(
    (path for path in edge_root.iterdir() if path.is_dir() and path.name[0].isdigit()),
    key=lambda path: tuple(int(part) for part in path.name.split(".")),
)
if not edge_versions or not (edge_root / "msedge.exe").is_file():
    raise RuntimeError("Microsoft Edge runtime missing from build computer")
edge_executable = edge_root / "msedge.exe"
edge_version_name = subprocess.check_output(
    ["powershell.exe", "-NoProfile", "-Command",
     f"(Get-Item -LiteralPath '{edge_executable}').VersionInfo.ProductVersion"],
    text=True,
).strip()
edge_version = edge_root / edge_version_name
if not edge_version.is_dir():
    raise RuntimeError(f"Matching Edge runtime version missing: {edge_version_name}")
datas.append((str(edge_root / "msedge.exe"), "edge-runtime"))
datas.append((str(edge_version), f"edge-runtime/{edge_version.name}"))

a = Analysis(
    [str(tool_dir / "main.py")],
    pathex=[str(tool_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pandas", "numpy", "aiohttp"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ozon_RFBS上品工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Ozon_RFBS上品工具",
)
