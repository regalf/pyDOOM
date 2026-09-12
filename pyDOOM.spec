# -*- mode: python ; coding: utf-8 -*-
"""pyDOOM onedir bundle (entry: pyDOOM.py launcher).

Build: python -m PyInstaller pyDOOM.spec
Run:   ./dist/pyDOOM/pyDOOM
Ships DOOM1.WAD (freely distributable shareware, under _internal/);
drop a full doom.wad next to it to unlock E1-E3 (never bundle it:
commercial IWAD, copyright). numba/PyOPL are optional: without
them the game runs the pure-python raster in silence.
"""

block_cipher = None


a = Analysis(
    ['pyDOOM.py'],
    pathex=[],
    binaries=[],
    datas=[('DOOM1.WAD', '.')],
    hiddenimports=['tools.doom_view'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='pyDOOM',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='pyDOOM',
)
