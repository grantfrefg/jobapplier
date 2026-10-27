# PyInstaller spec for the ApplyPilot desktop app (Windows .exe / macOS .app).
# Build from the repo root:  pyinstaller packaging/ApplyPilot.spec --noconfirm
# Output: dist/ApplyPilot/ (Windows/Linux folder) or dist/ApplyPilot.app (macOS)

import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# Our package (YAML registries, UI page, profile template), the job-board
# scraper and its native curl library, uvicorn's dynamically imported bits,
# pypdf (reads your own resume PDFs) and the Claude SDK (when Claude is the
# chosen AI provider); both are only imported when needed
for pkg in ("applypilot", "jobspy", "curl_cffi", "uvicorn", "pypdf", "anthropic"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Time zone data for the daily Gemini budget (Windows has no system copy)
try:
    d, b, h = collect_all("tzdata")
    datas += d
    hiddenimports += h
except Exception:
    pass  # budget.py falls back to a fixed US Pacific offset

hiddenimports += collect_submodules("applypilot")
hiddenimports += ["applypilot.__main__"]

a = Analysis(
    ["desktop_entry.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ApplyPilot",
    console=False,  # no black terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ApplyPilot")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ApplyPilot.app",
        bundle_identifier="com.applypilot.desktop",
        info_plist={
            "CFBundleShortVersionString": "0.4.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # No window of its own (the UI is a browser tab): run as a background
            # agent so the Dock icon doesn't bounce and show "not responding".
            "LSUIElement": True,
        },
    )
