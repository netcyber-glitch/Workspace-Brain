# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

from pathlib import Path

ROOT = Path.cwd()


def _p(rel: str) -> str:
    return str((ROOT / rel).resolve())


a = Analysis(
    [
        _p("workspace_brain_gui.py"),
        _p("scan_all.py"),
        _p("build_version_chains.py"),
        _p("search_cli.py"),
        _p("version_chain_overrides.py"),
    ],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_rthooks = [s for s in a.scripts if str(s[0] or "").startswith("pyi_rth_")]


def _pick(script_name: str):
    for s in a.scripts:
        if str(s[0] or "") == str(script_name):
            return s
    raise ValueError(f"script not found in a.scripts: {script_name}")


exe_gui = EXE(
    pyz,
    _rthooks + [_pick("workspace_brain_gui")],
    a.binaries,
    a.zipfiles,
    a.datas,
    name="Workspace-Brain",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    exclude_binaries=True,
)

exe_scan_all = EXE(
    pyz,
    _rthooks + [_pick("scan_all")],
    a.binaries,
    a.zipfiles,
    a.datas,
    name="scan_all",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    exclude_binaries=True,
)

exe_build_version_chains = EXE(
    pyz,
    _rthooks + [_pick("build_version_chains")],
    a.binaries,
    a.zipfiles,
    a.datas,
    name="build_version_chains",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    exclude_binaries=True,
)

exe_search_cli = EXE(
    pyz,
    _rthooks + [_pick("search_cli")],
    a.binaries,
    a.zipfiles,
    a.datas,
    name="search_cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    exclude_binaries=True,
)

exe_version_chain_overrides = EXE(
    pyz,
    _rthooks + [_pick("version_chain_overrides")],
    a.binaries,
    a.zipfiles,
    a.datas,
    name="version_chain_overrides",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    exclude_binaries=True,
)

coll = COLLECT(
    exe_gui,
    exe_scan_all,
    exe_build_version_chains,
    exe_search_cli,
    exe_version_chain_overrides,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="Workspace-Brain",
)
