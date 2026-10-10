# Build with: python -m PyInstaller --noconfirm Svaram.spec
from PyInstaller.utils.hooks import collect_submodules

a=Analysis(['main.py'],pathex=[],binaries=[],datas=[('shobotics-logo.svg','.')],
    hiddenimports=collect_submodules('bleak.backends.corebluetooth'),
    excludes=['PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtWebEngineQuick',
              'PySide6.QtQml','PySide6.QtQuick','PySide6.Qt3DCore','tkinter','matplotlib','IPython',
              'numpy','scipy'])
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='Svaram H10',debug=False,
        strip=False,upx=False,console=False,argv_emulation=False,target_arch='arm64',codesign_identity=None)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='Svaram H10')
app=BUNDLE(coll,name='Svaram H10.app',bundle_identifier='org.shobotics.svaram.h10',
    info_plist={'CFBundleDisplayName':'Sonorium','CFBundleShortVersionString':'0.3.0','NSHighResolutionCapable':True,
    'NSBluetoothAlwaysUsageDescription':'Sonorium reads heart rate, RR intervals and ECG from your Polar H10. Recordings stay on this Mac.',
    'NSBluetoothPeripheralUsageDescription':'Connect to your Polar H10.',
    'LSMinimumSystemVersion':'14.0'})
