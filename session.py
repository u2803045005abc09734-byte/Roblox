"""Isolierte Sitzungen pro Account + echte Anmeldestatus-Prüfung.

So funktioniert der Wechsel ohne unsichere Tricks:

* Jeder Account bekommt sein eigenes, dauerhaftes Browser-Profil (QtWebEngine).
  Du meldest dich EINMAL ganz normal auf roblox.com an (inkl. Passwort/2FA/Captcha
  auf der offiziellen Seite). Roblox' eigene Sitzung bleibt in genau diesem Profil.
* Dieses Programm liest, kopiert oder speichert keine Cookies oder Tokens.
* Den Anmeldestatus prüft ein unsichtbares Browser-Fenster im selben Profil, das die
  offizielle Roblox-Adresse https://users.roblox.com/v1/users/authenticated lädt.
  Roblox antwortet dort mit der ID/dem Namen des angemeldeten Users - oder mit einem
  Fehler, wenn niemand angemeldet ist. Nur das Ergebnis wird ausgewertet.
* "Spielen" auf der Website öffnet den installierten Roblox Player über das
  Roblox-eigene Protokoll - mit dem Account des jeweiligen Fensters.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QLabel, QMainWindow, QToolBar

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView

    HAVE_WEBENGINE = True
except Exception:  # pragma: no cover - hängt von der Installation ab
    HAVE_WEBENGINE = False
    QWebEnginePage = QWebEngineProfile = QWebEngineView = None  # type: ignore

STATUS_URL = os.environ.get("RAS_STATUS_URL", "https://users.roblox.com/v1/users/authenticated")
LOGIN_URL = os.environ.get("RAS_LOGIN_URL", "https://www.roblox.com/login")
HOME_URL = os.environ.get("RAS_HOME_URL", "https://www.roblox.com/home")
CHECK_TIMEOUT_MS = 20000


def parse_status(text: str) -> tuple[str, int, str]:
    """Wertet die Antwort der Status-Adresse aus.

    Rückgabe: (zustand, user_id, name) mit zustand in
    "verified" | "loggedout" | "unknown".
    """
    try:
        data = json.loads((text or "").strip())
    except ValueError:
        return "unknown", 0, ""
    if isinstance(data, dict):
        uid, name = data.get("id"), data.get("name")
        if isinstance(uid, int) and not isinstance(uid, bool) and uid > 0 and isinstance(name, str) and name:
            return "verified", uid, name
        if "errors" in data:
            return "loggedout", 0, ""
    return "unknown", 0, ""


class ProfileManager(QObject):
    """Ein dauerhaftes, getrenntes Browser-Profil pro Account."""

    def __init__(self, store, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.store = store
        self._profiles: dict[str, "QWebEngineProfile"] = {}

    def profile(self, account_id: str) -> "QWebEngineProfile":
        prof = self._profiles.get(account_id)
        if prof is None:
            base = self.store.profile_dir(account_id)
            (base / "storage").mkdir(parents=True, exist_ok=True)
            (base / "cache").mkdir(parents=True, exist_ok=True)
            prof = QWebEngineProfile(account_id, self)
            prof.setPersistentStoragePath(str(base / "storage"))
            prof.setCachePath(str(base / "cache"))
            prof.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
            self._profiles[account_id] = prof
        return prof

    def release_all(self) -> None:
        """Beim Beenden: Profile freigeben, damit die Browser-Engine Sitzungen sicher auf die Platte schreibt."""
        for account_id in list(self._profiles):
            self.release(account_id)

    def release(self, account_id: str) -> None:
        prof = self._profiles.pop(account_id, None)
        if prof is not None:
            prof.deleteLater()


class StatusChecker(QObject):
    """Prüft im Hintergrund (max. `concurrency` gleichzeitig), wer wirklich angemeldet ist."""

    result = Signal(str, str, int, str)  # account_id, zustand, user_id, name

    def __init__(self, profiles: ProfileManager, concurrency: int = 3, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.profiles = profiles
        self.concurrency = concurrency
        self._queue: list[str] = []
        self._active: dict[str, tuple] = {}

    def is_busy(self, account_id: str) -> bool:
        return account_id in self._active or account_id in self._queue

    def check(self, account_id: str, priority: bool = False) -> None:
        if self.is_busy(account_id):
            return
        if priority:
            self._queue.insert(0, account_id)
        else:
            self._queue.append(account_id)
        self._pump()

    def cancel(self, account_id: str) -> None:
        if account_id in self._queue:
            self._queue.remove(account_id)
        entry = self._active.pop(account_id, None)
        if entry:
            page, timer = entry
            timer.stop()
            timer.deleteLater()
            page.deleteLater()

    def _pump(self) -> None:
        while self._queue and len(self._active) < self.concurrency:
            self._start(self._queue.pop(0))

    def _start(self, account_id: str) -> None:
        page = QWebEnginePage(self.profiles.profile(account_id), self)
        timer = QTimer(self)
        timer.setSingleShot(True)
        self._active[account_id] = (page, timer)
        page.loadFinished.connect(lambda ok, a=account_id: self._on_loaded(a))
        timer.timeout.connect(lambda a=account_id: self._finish(a, "unknown", 0, ""))
        timer.start(CHECK_TIMEOUT_MS)
        page.load(QUrl(STATUS_URL))

    def _on_loaded(self, account_id: str) -> None:
        entry = self._active.get(account_id)
        if entry:
            entry[0].toPlainText(lambda text, a=account_id: self._finish(a, *parse_status(text)))

    def _finish(self, account_id: str, state: str, user_id: int, name: str) -> None:
        entry = self._active.pop(account_id, None)
        if entry is None:
            return
        page, timer = entry
        timer.stop()
        timer.deleteLater()
        page.deleteLater()
        self.result.emit(account_id, state, user_id, name)
        self._pump()


if HAVE_WEBENGINE:

    class RobloxPage(QWebEnginePage):
        def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802
            # "Spielen" auf roblox.com: Roblox Player über das offizielle Protokoll starten.
            if url.scheme() in ("roblox-player", "roblox"):
                if self.url().host().endswith("roblox.com"):
                    QDesktopServices.openUrl(url)
                return False
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class SessionWindow(QMainWindow):
    """Browserfenster im isolierten Profil genau eines Accounts."""

    loaded = Signal(str, QUrl)  # account_id, url
    closing = Signal(str)

    def __init__(self, account_id: str, title: str, profile, start_url: str):
        super().__init__()
        self.account_id = account_id
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(title)
        self.resize(1100, 760)
        self.view = QWebEngineView(self)
        self.page_obj = RobloxPage(profile, self.view)
        self.view.setPage(self.page_obj)
        self.setCentralWidget(self.view)

        bar = QToolBar(self)
        bar.setMovable(False)
        self.addToolBar(bar)
        for text, slot in (
            ("←", self.view.back),
            ("→", self.view.forward),
            ("⟳", self.view.reload),
            ("Start", lambda: self.view.setUrl(QUrl(HOME_URL))),
        ):
            act = QAction(text, self)
            act.triggered.connect(slot)
            bar.addAction(act)
        self.url_label = QLabel("")
        bar.addWidget(self.url_label)
        self.view.urlChanged.connect(lambda u: self.url_label.setText("  " + u.toString()))
        self.view.loadFinished.connect(lambda ok: self.loaded.emit(self.account_id, self.view.url()))
        self.view.setUrl(QUrl(start_url))

    def set_title(self, title: str) -> None:
        self.setWindowTitle(title)

    def closeEvent(self, event):  # noqa: N802
        self.closing.emit(self.account_id)
        super().closeEvent(event)
