# PyInstaller build file:  pyinstaller OrderFormApp.spec
from PyInstaller.utils.hooks import collect_submodules

datas = [
    ("orderapp/templates", "orderapp/templates"),
    ("orderapp/static", "orderapp/static"),
    ("orderapp/forms", "orderapp/forms"),
]
hidden = collect_submodules("orderapp") + ["win32com", "win32com.client", "pythoncom", "pywintypes"]

a = Analysis(["run.py"], pathex=["."], datas=datas, hiddenimports=hidden, excludes=["tkinter", "numpy", "pandas", "matplotlib", "scipy", "IPython", "playwright", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="OrderFormApp", console=False, upx=False)
