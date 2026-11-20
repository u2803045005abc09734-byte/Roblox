"""Echter Test der Sitzungs-Isolation und der Anmeldestatus-Prüfung (QtWebEngine).

Ein lokaler Mini-Server ahmt die Roblox-Status-Adresse nach:
  /login?u=NAME      -> setzt ein Sitzungs-Cookie
  /authenticated     -> JSON mit ID/Name, wenn das Cookie gültig ist, sonst 401 + errors

Modi:  all (Isolation, ein Prozess)  |  login DIR  |  check DIR (Persistenz über Neustart)
"""
import http.server
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

USERS = {"alice": 111, "bob": 222}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/login":
            name = query.split("=", 1)[1]
            self.send_response(200)
            self.send_header("Set-Cookie", f"sid={name}; Path=/; Max-Age=3600")
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"logged in")
            return
        cookie = self.headers.get("Cookie", "")
        sid = dict(p.strip().split("=", 1) for p in cookie.split(";") if "=" in p).get("sid")
        if path == "/authenticated" and sid in USERS:
            body, code = json.dumps({"id": USERS[sid], "name": sid, "displayName": sid}), 200
        else:
            body, code = json.dumps({"errors": [{"code": 0, "message": "Authorization has been denied for this request."}]}), 401
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_port}"
os.environ["RAS_STATUS_URL"] = BASE + "/authenticated"

from PySide6.QtCore import QEventLoop, QTimer, QUrl  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import session  # noqa: E402
from accounts_store import AccountStore  # noqa: E402

qa = QApplication([])


def wait(signal_owner_signal, timeout_ms=15000):
    loop = QEventLoop()
    signal_owner_signal.connect(lambda *a: loop.quit())
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


def login(profiles, account_id, name):
    page = QWebEnginePage(profiles.profile(account_id), qa)
    done = []
    page.loadFinished.connect(lambda ok: done.append(ok))
    page.load(QUrl(f"{BASE}/login?u={name}"))
    while not done:
        wait(page.loadFinished, 15000)
    assert done[0], "Login-Seite nicht geladen"
    page.deleteLater()


def check(checker, account_id):
    results = []
    checker.result.connect(lambda *a: results.append(a))
    checker.check(account_id, priority=True)
    for _ in range(3):
        if results:
            break
        wait(checker.result, 25000)
    assert results, "keine Antwort der Status-Prüfung"
    return results[-1]


def run_all():
    store = AccountStore(Path(tempfile.mkdtemp()))
    a, b, c = store.add("A"), store.add("B"), store.add("C")
    profiles = session.ProfileManager(store, qa)
    checker = session.StatusChecker(profiles, 3, qa)

    # Vorher: niemand angemeldet
    for acc in (a, b):
        r = check(checker, acc.id)
        assert r[1] == "loggedout", r

    # nur A meldet sich an -> B und C bleiben abgemeldet (Isolation)
    login(profiles, a.id, "alice")
    ra, rb, rc = check(checker, a.id), check(checker, b.id), check(checker, c.id)
    assert ra[1:] == ("verified", 111, "alice"), ra
    assert rb[1] == "loggedout" and rc[1] == "loggedout", (rb, rc)

    # B meldet sich als anderer User an -> beide gleichzeitig angemeldet, getrennt
    login(profiles, b.id, "bob")
    ra, rb = check(checker, a.id), check(checker, b.id)
    assert ra[1:] == ("verified", 111, "alice"), ra
    assert rb[1:] == ("verified", 222, "bob"), rb

    # parallele Prüfung vieler Einträge über die Warteschlange
    extra = [store.add(f"X{i}") for i in range(8)]
    results = []
    checker.result.connect(lambda *x: results.append(x))
    for acc in extra:
        checker.check(acc.id)
    for _ in range(20):
        if len(results) >= len(extra):
            break
        wait(checker.result, 25000)
    got = [r for r in results if r[0] in {e.id for e in extra}]
    assert len(got) == len(extra) and all(r[1] == "loggedout" for r in got), got
    print("ISOLATION OK")


def run_login(datadir):
    store = AccountStore(Path(datadir))
    acc = store.add("Persist")
    profiles = session.ProfileManager(store, qa)
    login(profiles, acc.id, "alice")
    checker = session.StatusChecker(profiles, 1, qa)
    assert check(checker, acc.id)[1] == "verified"
    # wie beim Beenden der App: Profile freigeben, Event-Loop kurz laufen lassen
    profiles.release_all()
    for _ in range(40):
        qa.processEvents()
        time.sleep(0.05)
    print("LOGIN DONE")


def run_check(datadir):
    store = AccountStore(Path(datadir))
    acc = store.view()[0]
    profiles = session.ProfileManager(store, qa)
    checker = session.StatusChecker(profiles, 1, qa)
    r = check(checker, acc.id)
    assert r[1:] == ("verified", 111, "alice"), f"Sitzung nach Neustart nicht erhalten: {r}"
    print("PERSISTENCE OK")


mode = sys.argv[1] if len(sys.argv) > 1 else "all"
{"all": run_all, "login": lambda: run_login(sys.argv[2]), "check": lambda: run_check(sys.argv[2])}[mode]()
if mode == "login":
    del qa  # normales Beenden (Chromium schreibt die Sitzung weg)
    sys.exit(0)
os._exit(0)
