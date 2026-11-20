"""GUI-Smoke-Test ohne Anzeige: 500 Accounts, Klicks, Suche, Sortierung, Screenshot."""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import app as appmod  # noqa: E402
from accounts_store import AccountStore  # noqa: E402

N = 500
out_png = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("gui_smoke.png")

qa = QApplication([])
qa.setStyle("Fusion")
qa.setStyleSheet(appmod.STYLE)

tmp = Path(tempfile.mkdtemp())
store = AccountStore(tmp)
ids = []
with store.batch():
    for i in range(N):
        a = store.add(f"Account {i:03d}")
        ids.append(a.id)
        if i % 3 == 0:
            store.set_identity(a.id, f"RobloxUser{i}", 1000 + i)
        if i % 50 == 7:
            store.set_favorite(a.id, True)
        if i < 6:
            store.mark_used(a.id)
            time.sleep(0.002)

# eigenes Profilbild für den ersten Account
img = QImage(120, 120, QImage.Format.Format_RGB32)
img.fill(QColor("#7A5CFA"))
src = tmp / "pic.png"
img.save(str(src))
store.set_custom_avatar(ids[0], src)

win = appmod.MainWindow(store)
win.show()
qa.processEvents()
assert len(store) == N and win.model.rowCount() == N, "nicht alle Accounts in der Liste"

# Zustände simulieren (so wie sie der echte Status-Check liefern würde)
win.model.status[ids[3]] = "verified"
win.model.status[ids[6]] = "loggedout"
win.model.status[ids[9]] = "unknown"
win.pending_switch = ids[0]
win.on_status(ids[0], "verified", 1000, "RobloxUser0")
assert win.model.active_id == ids[0], "aktiver Account wurde nicht gesetzt"
win.on_status(ids[3], "loggedout", 0, "")
assert win.model.active_id == ids[0]
win.on_status(ids[0], "loggedout", 0, "")
assert win.model.active_id is None, "abgemeldeter Account darf nicht als aktiv gelten"
win.pending_switch = ids[0]
win.on_status(ids[0], "verified", 1000, "RobloxUser0")
assert win.model.active_id == ids[0]

# Doppelte Roblox-ID darf nichts überschreiben (Dialog unterdrücken)
appmod.QMessageBox.warning = staticmethod(lambda *a, **k: None)
win.on_status(ids[1], "verified", 1000, "RobloxUser0")
assert win.model.status[ids[1]] == "duplicate" and store.get(ids[1]).user_id == 0

# Klick auf Zeile -> switch_requested, Klick auf Stern -> favorite_toggled
got = {"switch": [], "fav": []}
win.view.switch_requested.connect(lambda i: got["switch"].append(i))
win.view.favorite_toggled.connect(lambda i: got["fav"].append(i))
idx = win.model.index(10)
target_id = idx.data(appmod.ACC_ROLE).id
rect = win.view.visualRect(idx)
QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(rect.left() + 100, rect.center().y()))
assert got["switch"] == [target_id], got
assert got["fav"] == [], got
idx = win.model.index(win.model.row_of(target_id))
r2 = win.view.visualRect(idx).adjusted(4, 3, -4, -3)
before = len(got["switch"])
QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, pos=appmod.AccountDelegate.star_rect(r2).center())
assert got["fav"] == [target_id], got
assert len(got["switch"]) == before, "Stern-Klick darf keinen Wechsel auslösen"
assert store.get(target_id).favorite is True

# Tempo: Suche / Sortierung / Scrollen
t = time.perf_counter()
for text in ("1", "12", "Account 4", ""):
    win.search.setText(text)
    qa.processEvents()
for i in range(win.sort.count()):
    win.sort.setCurrentIndex(i)
    qa.processEvents()
sb = win.view.verticalScrollBar()
for step in range(0, sb.maximum(), max(sb.maximum() // 60, 1)):
    sb.setValue(step)
    qa.processEvents()
dur = time.perf_counter() - t
print(f"Suche+Sortierung+Scrollen bei {N} Accounts: {dur:.2f}s")
assert dur < 8.0

win.search.setText("")
win.sort.setCurrentIndex(0)
sb.setValue(0)
qa.processEvents()
from PySide6.QtWidgets import QPushButton  # noqa: E402
chips = [b for b in win.findChildren(QPushButton) if b.objectName() == "chip"]
assert len(chips) == len(store.recent(5)), f"verwaiste Chips: {len(chips)}"
win.grab().save(str(out_png))
win.shutdown()
print("OK", out_png)
