"""Roblox Account Switcher - Hauptfenster.

Start:  python app.py
"""
from __future__ import annotations

import faulthandler
import json
import sys
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from accounts_store import (
    Account,
    AccountStore,
    DuplicateAccountError,
    StoreCorruptError,
)
import session
from session import HAVE_WEBENGINE, LOGIN_URL, HOME_URL, ProfileManager, SessionWindow, StatusChecker

ACC_ROLE = Qt.ItemDataRole.UserRole
ACCENT = QColor("#FF8A2B")
GREEN = QColor("#3DDC84")
RED = QColor("#FF6B6B")
MUTED = QColor("#8B8FA3")

STYLE = """
QWidget { background:#14151A; color:#EDEDF0; font-size:13px; }
QLineEdit, QComboBox { background:#1E2028; border:1px solid #2C2F3A; border-radius:8px; padding:7px 10px; }
QLineEdit:focus, QComboBox:focus { border:1px solid #FF8A2B; }
QComboBox QAbstractItemView { background:#1E2028; selection-background-color:#2C2F3A; border:1px solid #2C2F3A; }
QPushButton { background:#FF8A2B; color:#1A1208; border:none; border-radius:8px; padding:8px 14px; font-weight:600; }
QPushButton:hover { background:#FFA04F; }
QPushButton#chip { background:#23252F; color:#EDEDF0; padding:5px 11px; font-weight:500; border-radius:12px; }
QPushButton#chip:hover { background:#2E313D; }
QPushButton#ghost { background:#23252F; color:#EDEDF0; font-weight:500; }
QPushButton#ghost:hover { background:#2E313D; }
QListView { border:none; background:transparent; outline:0; }
QScrollBar:vertical { background:transparent; width:10px; margin:2px; }
QScrollBar::handle:vertical { background:#2C2F3A; border-radius:4px; min-height:30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QMenu { background:#1E2028; border:1px solid #2C2F3A; padding:4px; }
QMenu::item { padding:6px 18px; border-radius:6px; }
QMenu::item:selected { background:#2C2F3A; }
"""

SORT_OPTIONS = [("Zuletzt verwendet", "recent"), ("A – Z", "alpha"), ("Zuletzt hinzugefügt", "created")]


# --------------------------------------------------------------------------- Avatar-Download
class AvatarSignals(QObject):
    done = Signal(str, str)  # account_id, dateiname


class AvatarJob(QRunnable):
    """Lädt das öffentliche Profilbild (Kopfbild) über die offizielle Roblox-Thumbnail-API."""

    def __init__(self, avatars_dir: Path, account_id: str, user_id: int, signals: AvatarSignals):
        super().__init__()
        self.avatars_dir, self.account_id, self.user_id, self.signals = avatars_dir, account_id, user_id, signals

    def run(self) -> None:
        try:
            api = (
                "https://thumbnails.roblox.com/v1/users/avatar-headshot"
                f"?userIds={int(self.user_id)}&size=150x150&format=Png&isCircular=false"
            )
            with urllib.request.urlopen(api, timeout=10) as resp:
                data = json.load(resp)
            url = data["data"][0]["imageUrl"]
            host = urlparse(url).hostname or ""
            if urlparse(url).scheme != "https" or not host.endswith(".rbxcdn.com"):
                return
            with urllib.request.urlopen(url, timeout=10) as resp:
                img = resp.read(2_000_000)
            name = f"{self.account_id}-roblox.png"
            (self.avatars_dir / name).write_bytes(img)
            self.signals.done.emit(self.account_id, name)
        except Exception:
            pass  # Avatar ist optional


# --------------------------------------------------------------------------- Modell
class AccountModel(QAbstractListModel):
    def __init__(self, store: AccountStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.items: list[Account] = []
        self.status: dict[str, str] = {}  # id -> verified | loggedout | checking | unknown | duplicate
        self.active_id: Optional[str] = None

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.items)):
            return None
        acc = self.items[index.row()]
        if role == ACC_ROLE:
            return acc
        if role == Qt.ItemDataRole.DisplayRole:
            return acc.title
        return None

    def set_items(self, items: list[Account]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def row_of(self, account_id: str) -> int:
        for i, a in enumerate(self.items):
            if a.id == account_id:
                return i
        return -1

    def touch(self, account_id: str) -> None:
        row = self.row_of(account_id)
        if row >= 0:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)

    def touch_all(self) -> None:
        if self.items:
            self.dataChanged.emit(self.index(0), self.index(len(self.items) - 1))


# --------------------------------------------------------------------------- Darstellung
def circular_pixmap(path: Path, size: int) -> Optional[QPixmap]:
    src = QPixmap(str(path))
    if src.isNull():
        return None
    src = src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addEllipse(0, 0, size, size)
    p.setClipPath(clip)
    p.drawPixmap(-(src.width() - size) // 2, -(src.height() - size) // 2, src)
    p.end()
    return out


def font_variant(base: QFont, delta: float = 0, bold: bool = False) -> QFont:
    """Schrift relativ zur Basisschrift - funktioniert auch bei pixelbasierten Schriften."""
    f = QFont(base)
    if base.pointSizeF() > 0:
        f.setPointSizeF(max(base.pointSizeF() + delta, 7.0))
    elif base.pixelSize() > 0:
        f.setPixelSize(max(int(base.pixelSize() + delta * 1.33), 9))
    f.setBold(bold)
    return f


class AccountDelegate(QStyledItemDelegate):
    ROW_H = 64
    AVATAR = 44

    def __init__(self, model: AccountModel, parent=None):
        super().__init__(parent)
        self.model = model
        self._cache: dict[str, Optional[QPixmap]] = {}

    def invalidate(self, filename: str) -> None:
        self._cache.pop(filename, None)

    def _avatar(self, acc: Account) -> Optional[QPixmap]:
        if not acc.avatar:
            return None
        if acc.avatar not in self._cache:
            self._cache[acc.avatar] = circular_pixmap(self.model.store.avatars_dir / acc.avatar, self.AVATAR)
        return self._cache[acc.avatar]

    @staticmethod
    def star_rect(rect: QRect) -> QRect:
        return QRect(rect.right() - 44, rect.center().y() - 14, 28, 28)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(0, self.ROW_H)

    def status_text(self, acc: Account) -> tuple[str, QColor]:
        st = self.model.status.get(acc.id, "")
        if acc.id == self.model.active_id and st == "verified":
            return "AKTIV", GREEN
        return {
            "verified": ("Angemeldet", GREEN),
            "loggedout": ("Nicht angemeldet", RED),
            "checking": ("Prüfe …", ACCENT),
            "duplicate": ("Doppelt", RED),
            "unknown": ("Status unklar", MUTED),
        }.get(st, ("Nicht geprüft", MUTED))

    def paint(self, p: QPainter, option, index) -> None:
        acc: Account = index.data(ACC_ROLE)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(4, 3, -4, -3)
        active = acc.id == self.model.active_id and self.model.status.get(acc.id) == "verified"
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        bg = QColor("#262833") if selected else QColor("#1D1F27") if hover else QColor("#181A21")
        p.setBrush(QBrush(bg))
        p.setPen(QPen(ACCENT, 1.5) if active else Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 12, 12)

        # Avatar
        ax, ay = r.left() + 12, r.center().y() - self.AVATAR // 2
        pm = self._avatar(acc)
        if pm is not None:
            p.drawPixmap(ax, ay, pm)
        else:
            hue = (hash(acc.id) & 0xFFFF) % 360
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor.fromHsl(hue, 120, 90))
            p.drawEllipse(ax, ay, self.AVATAR, self.AVATAR)
            p.setFont(font_variant(option.font, 3, True))
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRect(ax, ay, self.AVATAR, self.AVATAR), Qt.AlignmentFlag.AlignCenter, acc.title[:1].upper())

        # Star
        star = self.star_rect(r)
        p.setFont(font_variant(option.font, 5))
        p.setPen(ACCENT if acc.favorite else QColor("#4A4D5C"))
        p.drawText(star, Qt.AlignmentFlag.AlignCenter, "★" if acc.favorite else "☆")

        # Badge
        text, color = self.status_text(acc)
        bf = font_variant(option.font, -1, True)
        fm = QFontMetrics(bf)
        bw = fm.horizontalAdvance(text) + 18
        badge = QRect(star.left() - bw - 8, r.center().y() - 11, bw, 22)
        p.setFont(bf)
        fill = QColor(color)
        fill.setAlpha(40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(badge, 11, 11)
        p.setPen(color)
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

        # Titel / Untertitel
        tx = ax + self.AVATAR + 14
        tw = max(badge.left() - tx - 10, 20)
        tf = font_variant(option.font, 1, True)
        p.setFont(tf)
        p.setPen(QColor("#F4F4F7"))
        title = QFontMetrics(tf).elidedText(acc.title, Qt.TextElideMode.ElideRight, tw)
        p.drawText(QRect(tx, r.top() + 10, tw, 22), Qt.AlignmentFlag.AlignVCenter, title)
        sub = f"@{acc.username}" if acc.username else "noch nicht angemeldet"
        if acc.last_used:
            sub += "  ·  zuletzt " + time.strftime("%d.%m.%Y %H:%M", time.localtime(acc.last_used))
        sf = font_variant(option.font, -1)
        p.setFont(sf)
        p.setPen(MUTED)
        p.drawText(
            QRect(tx, r.top() + 32, tw, 20),
            Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(sf).elidedText(sub, Qt.TextElideMode.ElideRight, tw),
        )
        p.restore()


class AccountListView(QListView):
    switch_requested = Signal(str)
    favorite_toggled = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)  # wichtig für Tempo bei sehr vielen Accounts
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalScrollBar().setSingleStep(24)

    def mouseReleaseEvent(self, e):  # noqa: N802
        super().mouseReleaseEvent(e)
        if e.button() != Qt.MouseButton.LeftButton:
            return
        idx = self.indexAt(e.position().toPoint())
        if not idx.isValid():
            return
        acc: Account = idx.data(ACC_ROLE)
        rect = self.visualRect(idx).adjusted(4, 3, -4, -3)
        if AccountDelegate.star_rect(rect).contains(e.position().toPoint()):
            self.favorite_toggled.emit(acc.id)
        else:
            self.switch_requested.emit(acc.id)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.switch_requested.emit(self.currentIndex().data(ACC_ROLE).id)
            return
        super().keyPressEvent(e)


# --------------------------------------------------------------------------- Hauptfenster
class MainWindow(QWidget):
    def __init__(self, store: AccountStore):
        super().__init__()
        self.store = store
        self.setWindowTitle("Roblox Account Switcher")
        self.resize(560, 760)
        self.model = AccountModel(store, self)
        self.profiles = ProfileManager(store, self) if HAVE_WEBENGINE else None
        self.checker = StatusChecker(self.profiles, 3, self) if HAVE_WEBENGINE else None
        if self.checker:
            self.checker.result.connect(self.on_status)
        self.windows: dict[str, SessionWindow] = {}
        self.pending_switch: Optional[str] = None
        self.avatar_signals = AvatarSignals(self)
        self.avatar_signals.done.connect(self.on_avatar)
        self._debounce: dict[str, float] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Accounts durchsuchen …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh())
        self.sort = QComboBox()
        for label, key in SORT_OPTIONS:
            self.sort.addItem(label, key)
        self.sort.currentIndexChanged.connect(lambda _: self.refresh())
        add = QPushButton("+ Account")
        add.clicked.connect(self.add_account)
        top.addWidget(self.search, 1)
        top.addWidget(self.sort)
        top.addWidget(add)
        root.addLayout(top)

        self.recent_row = QHBoxLayout()
        self.recent_row.setSpacing(6)
        root.addLayout(self.recent_row)

        self.view = AccountListView()
        self.delegate = AccountDelegate(self.model, self.view)
        self.view.setModel(self.model)
        self.view.setItemDelegate(self.delegate)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.show_menu)
        self.view.switch_requested.connect(self.switch_to)
        self.view.favorite_toggled.connect(self.toggle_favorite)
        root.addWidget(self.view, 1)

        bottom = QHBoxLayout()
        self.footer = QLabel("")
        self.footer.setStyleSheet(f"color:{MUTED.name()};")
        check_all = QPushButton("Alle prüfen")
        check_all.setObjectName("ghost")
        check_all.clicked.connect(self.check_all)
        bottom.addWidget(self.footer, 1)
        bottom.addWidget(check_all)
        root.addLayout(bottom)

        self.refresh()

    # ---- Beenden
    def shutdown(self) -> None:
        if self.checker:
            for aid in list(self.checker._active) + list(self.checker._queue):
                self.checker.cancel(aid)
        for win in list(self.windows.values()):
            win.close()
        self.windows.clear()
        QApplication.processEvents()
        if self.profiles:
            self.profiles.release_all()
            for _ in range(20):  # Event-Loop kurz laufen lassen, damit Sitzungen gespeichert werden
                QApplication.processEvents()
                time.sleep(0.05)

    def closeEvent(self, event):  # noqa: N802
        self.shutdown()
        super().closeEvent(event)

    # ---- Anzeige
    def refresh(self, keep: Optional[str] = None) -> None:
        if keep is None and self.view.currentIndex().isValid():
            keep = self.view.currentIndex().data(ACC_ROLE).id
        items = self.store.view(self.search.text(), self.sort.currentData())
        self.model.set_items(items)
        if keep:
            row = self.model.row_of(keep)
            if row >= 0:
                self.view.setCurrentIndex(self.model.index(row))
        self.rebuild_recent()
        self.update_footer()

    def rebuild_recent(self) -> None:
        while self.recent_row.count():
            item = self.recent_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        recent = self.store.recent(5)
        if not recent:
            return
        lbl = QLabel("Zuletzt:")
        lbl.setStyleSheet(f"color:{MUTED.name()};")
        self.recent_row.addWidget(lbl)
        for acc in recent:
            b = QPushButton(acc.title[:18])
            b.setObjectName("chip")
            b.clicked.connect(lambda _=False, i=acc.id: self.switch_to(i))
            self.recent_row.addWidget(b)
        self.recent_row.addStretch(1)

    def update_footer(self) -> None:
        total, shown = len(self.store), len(self.model.items)
        txt = f"{total} Account{'s' if total != 1 else ''}"
        if shown != total:
            txt += f"  ·  {shown} angezeigt"
        active = self.store.get(self.model.active_id) if self.model.active_id else None
        if active and self.model.status.get(active.id) == "verified":
            txt += f"  ·  aktiv: {active.title}"
        self.footer.setText(txt)

    def notify(self, text: str) -> None:
        self.footer.setText(text)
        QTimer.singleShot(6000, self.update_footer)

    # ---- Aktionen
    def need_webengine(self) -> bool:
        if HAVE_WEBENGINE:
            return False
        QMessageBox.warning(
            self, "QtWebEngine fehlt",
            "Für Anmeldung und Wechsel wird QtWebEngine benötigt.\nInstallation: pip install PySide6",
        )
        return True

    def add_account(self) -> None:
        if self.need_webengine():
            return
        name, ok = QInputDialog.getText(self, "Account hinzufügen", "Anzeigename (optional):")
        if not ok:
            return
        acc = self.store.add(name)
        self.search.clear()
        self.refresh(keep=acc.id)
        self.open_window(acc, LOGIN_URL)
        self.pending_switch = acc.id
        self.notify("Bitte im neuen Fenster bei Roblox anmelden – der Status wird danach automatisch geprüft.")

    def open_window(self, acc: Account, url: str) -> None:
        win = self.windows.get(acc.id)
        if win is None:
            win = SessionWindow(acc.id, acc.title, self.profiles.profile(acc.id), url)
            win.loaded.connect(self.on_window_loaded)
            win.closing.connect(lambda aid: self.windows.pop(aid, None))
            self.windows[acc.id] = win
        win.show()
        win.raise_()
        win.activateWindow()

    def switch_to(self, account_id: str) -> None:
        acc = self.store.get(account_id)
        if acc is None or self.need_webengine():
            return
        self.pending_switch = account_id
        self.model.active_id = None  # aktiv wird erst nach bestätigter Anmeldung gesetzt
        self.model.status[account_id] = "checking"
        self.model.touch_all()
        self.update_footer()
        self.open_window(acc, HOME_URL if acc.user_id else LOGIN_URL)
        self.checker.check(account_id, priority=True)

    def check_all(self) -> None:
        if self.need_webengine():
            return
        for acc in self.store.view("", "alpha", favorites_first=False):
            self.model.status[acc.id] = "checking"
            self.checker.check(acc.id)
        self.model.touch_all()
        self.notify("Prüfe alle Accounts …")

    def toggle_favorite(self, account_id: str) -> None:
        acc = self.store.get(account_id)
        if acc:
            self.store.set_favorite(account_id, not acc.favorite)
            self.refresh(keep=account_id)

    def on_window_loaded(self, account_id: str, url) -> None:
        host, path = url.host(), url.path()
        if not host.endswith("roblox.com") or path.startswith(("/login", "/newlogin")):
            return
        now = time.time()
        if now - self._debounce.get(account_id, 0) < 2.0 or self.checker.is_busy(account_id):
            return
        self._debounce[account_id] = now
        self.checker.check(account_id, priority=True)

    def on_status(self, account_id: str, state: str, user_id: int, name: str) -> None:
        acc = self.store.get(account_id)
        if acc is None:
            return
        if state == "verified":
            try:
                self.store.set_identity(account_id, name, user_id)
            except DuplicateAccountError as dup:
                self.model.status[account_id] = "duplicate"
                if self.model.active_id == account_id:
                    self.model.active_id = None
                self.model.touch(account_id)
                QMessageBox.warning(
                    self, "Account bereits vorhanden",
                    f"„{name}“ ist schon als „{dup.existing.title}“ gespeichert.\n"
                    "Der Eintrag wurde nicht überschrieben. Du kannst diesen doppelten Eintrag entfernen.",
                )
                return
            self.model.status[account_id] = "verified"
            acc = self.store.get(account_id)
            if self.pending_switch == account_id:
                self.model.active_id = account_id
                self.store.mark_used(account_id)
                self.pending_switch = None
                win = self.windows.get(account_id)
                if win:
                    win.set_title(f"{acc.title} (@{acc.username})")
            if not acc.avatar:
                self.fetch_avatar(account_id)
            self.refresh(keep=account_id)
        elif state == "loggedout":
            self.model.status[account_id] = "loggedout"
            if self.model.active_id == account_id:
                self.model.active_id = None
            self.model.touch(account_id)
            if self.pending_switch == account_id:
                self.notify(f"„{acc.title}“ ist nicht angemeldet – bitte im geöffneten Fenster anmelden.")
        else:
            self.model.status[account_id] = "unknown"
            if self.model.active_id == account_id:
                self.model.active_id = None
            self.model.touch(account_id)
            if self.pending_switch == account_id:
                self.notify("Anmeldestatus konnte nicht geprüft werden (Netzwerk?).")
        self.update_footer()

    # ---- Avatar
    def fetch_avatar(self, account_id: str) -> None:
        acc = self.store.get(account_id)
        if acc and acc.user_id:
            QThreadPool.globalInstance().start(
                AvatarJob(self.store.avatars_dir, account_id, acc.user_id, self.avatar_signals)
            )

    def on_avatar(self, account_id: str, filename: str) -> None:
        acc = self.store.get(account_id)
        if acc is None:
            return
        self.store.set_avatar(account_id, filename)
        self.delegate.invalidate(filename)
        self.model.touch(account_id)

    # ---- Kontextmenü
    def show_menu(self, pos: QPoint) -> None:
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        acc: Account = idx.data(ACC_ROLE)
        menu = QMenu(self)
        act_switch = menu.addAction("Zu diesem Account wechseln")
        act_check = menu.addAction("Anmeldestatus prüfen")
        menu.addSeparator()
        act_fav = menu.addAction("Favorit entfernen" if acc.favorite else "Als Favorit markieren")
        act_rename = menu.addAction("Umbenennen …")
        act_pic = menu.addAction("Eigenes Profilbild …")
        act_roblox_pic = menu.addAction("Profilbild von Roblox laden")
        act_roblox_pic.setEnabled(bool(acc.user_id))
        menu.addSeparator()
        act_remove = menu.addAction("Entfernen …")
        chosen = menu.exec(self.view.viewport().mapToGlobal(pos))
        if chosen is act_switch:
            self.switch_to(acc.id)
        elif chosen is act_check and not self.need_webengine():
            self.model.status[acc.id] = "checking"
            self.model.touch(acc.id)
            self.checker.check(acc.id, priority=True)
        elif chosen is act_fav:
            self.toggle_favorite(acc.id)
        elif chosen is act_rename:
            name, ok = QInputDialog.getText(self, "Umbenennen", "Neuer Name:", text=acc.name)
            if ok:
                self.store.rename(acc.id, name)
                self.refresh(keep=acc.id)
        elif chosen is act_pic:
            path, _ = QFileDialog.getOpenFileName(self, "Profilbild wählen", "", "Bilder (*.png *.jpg *.jpeg *.webp *.bmp)")
            if path:
                fname = self.store.set_custom_avatar(acc.id, Path(path))
                self.delegate.invalidate(fname)
                self.model.touch(acc.id)
        elif chosen is act_roblox_pic:
            self.fetch_avatar(acc.id)
        elif chosen is act_remove:
            self.remove_account(acc)

    def remove_account(self, acc: Account) -> None:
        box = QMessageBox.question(
            self, "Account entfernen",
            f"„{acc.title}“ aus der Liste entfernen?\n\n"
            "Das gespeicherte Browser-Profil dieses Accounts wird gelöscht. "
            "Dein Roblox-Account selbst bleibt unverändert; andere Einträge sind nicht betroffen.",
        )
        if box != QMessageBox.StandardButton.Yes:
            return
        if self.checker:
            self.checker.cancel(acc.id)
        win = self.windows.pop(acc.id, None)
        if win:
            win.close()
        if self.model.active_id == acc.id:
            self.model.active_id = None
        self.model.status.pop(acc.id, None)
        if self.profiles:
            self.profiles.release(acc.id)
        QApplication.processEvents()
        self.store.remove(acc.id)
        self.delegate.invalidate(acc.avatar)
        self.refresh()


def setup_logging() -> None:
    """Schreibt Fehler und Abstürze in app.log (neben den Account-Daten), damit man sie auswerten kann."""
    try:
        from accounts_store import default_data_dir
        from PySide6.QtCore import qInstallMessageHandler

        folder = default_data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        log = open(folder / "app.log", "a", buffering=1, encoding="utf-8")
        log.write(f"\n=== Start {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        faulthandler.enable(file=log, all_threads=True)

        def hook(exc_type, exc, tb):
            traceback.print_exception(exc_type, exc, tb, file=log)
            log.flush()

        sys.excepthook = hook
        qInstallMessageHandler(lambda mode, ctx, msg: log.write(f"[Qt] {msg}\n"))
        sys._ras_log = log  # Referenz halten
    except Exception:
        pass


def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    try:
        store = AccountStore()
    except StoreCorruptError as exc:
        QMessageBox.critical(None, "Account-Datei beschädigt", str(exc))
        return 1
    win = MainWindow(store)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
