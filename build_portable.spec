# -*- mode: python ; coding: utf-8 -*-
# ac-dl v2.0.0 便携版构建 spec（onedir / console）
# 用法：pyinstaller build_portable.spec --noconfirm
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# 数据文件：N_m3u8DL-RE 下载器 + 配置示例（首次运行会自动生成 config.ini）
datas = [
    ('tools/N_m3u8DL-RE/N_m3u8DL-RE.exe', 'tools/N_m3u8DL-RE'),
    ('config.example.ini', '.'),
]

# Playwright 需要整体收集（含 Node 驱动 / JS），否则运行时会找不到 driver
pw_datas, pw_bins, pw_hiddenimports = collect_all('playwright')
datas += pw_datas
binaries = pw_bins
hiddenimports = pw_hiddenimports + ['bs4', 'requests', 'colorama']

a = Analysis(
    ['ac-dl.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ac-dl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
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
    name='ac-dl',
)
