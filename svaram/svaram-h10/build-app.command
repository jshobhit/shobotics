#!/bin/bash
# Build Svaram H10.app from source. Double-click in Finder; output goes to build.log.
cd "$(dirname "$0")"
PY=../.venv/bin/python
{
  echo "== $(date)"
  "$PY" --version
  echo "== tests"
  "$PY" -m pytest tests -q -p no:cacheprovider
  echo "== smoke run from source"
  "$PY" - <<'PYEOF'
import sys, time
from PySide6.QtWidgets import QApplication
from h10rig.ui import theme
from h10rig.ui.window import MainWindow
app = QApplication(sys.argv); app.setStyle("Fusion"); theme.apply(app)
w = MainWindow(); w.show()
end = time.monotonic() + 3
while time.monotonic() < end:
    app.processEvents(); time.sleep(0.01)
w.grab().save("smoke.png"); print("smoke ok, dpr", w.devicePixelRatioF())
w.ble.close()
PYEOF
  echo "== pyinstaller"
  rm -rf build dist
  "$PY" -m PyInstaller --noconfirm --clean Svaram.spec
  echo "== install"
  if [ -d "dist/Svaram H10.app" ]; then
    rm -rf "Svaram H10.app" && cp -R "dist/Svaram H10.app" "Svaram H10.app" && echo "installed Svaram H10.app"
  fi
  echo "== done $(date)"
} > build.log 2>&1
echo "Finished. See build.log"
