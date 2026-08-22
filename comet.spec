# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building a standalone Windows executable of Comet.

Build with:
    uv run pyinstaller comet.spec

The resulting binary lives in dist/comet/comet.exe (onedir keeps startup fast
and avoids antivirus false positives common with onefile builds).
"""

import os
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

hiddenimports = [
    # Dynamically discovered scraper plugins.
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

a = Analysis(
    ["comet/main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (ROOT / "comet" / "templates", "comet/templates"),
        (ROOT / "comet" / "assets", "comet/assets"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="comet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="comet",
)
