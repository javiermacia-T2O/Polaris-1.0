# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('C:/Users/javier.macia/Desktop/MEDICIÓN VSCODE/MedicionAgil_Light/mmm_app/analyses', 'analyses')]
binaries = []
hiddenimports = ['causalimpact', 'statsmodels.api', 'sklearn.linear_model', 'sklearn.ensemble', 'sklearn.preprocessing']
tmp_ret = collect_all('meridian_geox')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('jax')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('jaxkd')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('jaxlib')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tslearn')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:/Users/javier.macia/Desktop/MEDICIÓN VSCODE/MedicionAgil_Light/mmm_app/app_desktop.py'],
    pathex=['C:/Users/javier.macia/Desktop/MEDICIÓN VSCODE/MedicionAgil_Light/mmm_app', 'C:/Users/javier.macia/Desktop/MEDICIÓN VSCODE/.geox-deps'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MedicionAgil',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name='MedicionAgil',
)
