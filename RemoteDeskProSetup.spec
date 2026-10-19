# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for RemoteDeskPro-Setup.exe Windows Installer."""

block_cipher = None

datas = [
    ('dist/RemoteDeskPro.exe', 'payload'),
    ('assets/icon.ico', 'assets'),
]

a = Analysis(
    ['installer/setup_wizard.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=['winreg', 'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'PIL', 'fastapi', 'uvicorn', 'cryptography', 'psutil'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='RemoteDeskPro-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)
