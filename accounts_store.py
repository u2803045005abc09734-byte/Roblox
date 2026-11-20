"""Lokaler Account-Speicher für den Roblox Account Switcher.

Gespeichert werden ausschließlich Metadaten (Anzeigename, Roblox-Name, Avatar,
Favorit, Zeitstempel). Es werden NIE Passwörter, Cookies oder Tokens von diesem
Programm gelesen oder gespeichert. Die Anmeldung jedes Accounts lebt in einem
eigenen, isolierten Browser-Profil, das die Browser-Engine selbst verwaltet.

Eigenschaften:
- keine Obergrenze für die Anzahl der Accounts
- atomares Schreiben (temp-Datei + os.replace) mit .bak-Sicherung
- bei beschädigter Datei wird die Sicherung geladen; nichts wird still überschrieben
- jeder Account hat eine eigene UUID und ein eigenes Profilverzeichnis
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Iterator, Optional

SCHEMA_VERSION = 1
SORT_MODES = ("recent", "alpha", "created")


def default_data_dir() -> Path:
    override = os.environ.get("RAS_DATA_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "RobloxAccountSwitcher"


class StoreError(Exception):
    pass


class StoreCorruptError(StoreError):
    pass


class DuplicateAccountError(StoreError):
    """Dieselbe Roblox-User-ID ist bereits unter einem anderen Eintrag gespeichert."""

    def __init__(self, existing: "Account"):
        super().__init__(f"Roblox-Account bereits vorhanden: {existing.name}")
        self.existing = existing


@dataclass
class Account:
    id: str
    name: str                  # frei wählbarer Anzeigename
    username: str = ""         # verifizierter Roblox-Name (erst nach echter Anmeldung)
    user_id: int = 0           # verifizierte Roblox-User-ID
    avatar: str = ""           # Dateiname im Ordner avatars/
    favorite: bool = False
    created: float = 0.0
    last_used: float = 0.0     # 0 = noch nie benutzt

    @property
    def title(self) -> str:
        return self.name or self.username or "Unbenannt"

    @property
    def search_key(self) -> str:
        return f"{self.name}\n{self.username}".casefold()


_FIELD_NAMES = {f.name for f in fields(Account)}


class AccountStore:
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.profiles_dir = self.data_dir / "profiles"
        self.avatars_dir = self.data_dir / "avatars"
        self.path = self.data_dir / "accounts.json"
        self.backup_path = self.data_dir / "accounts.json.bak"
        for d in (self.data_dir, self.profiles_dir, self.avatars_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._accounts: dict[str, Account] = {}
        self._batch_depth = 0
        self._dirty = False
        self._load()

    # ------------------------------------------------------------ Laden/Speichern
    def _read_file(self, path: Path) -> dict[str, Account]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("accounts"), list):
            raise ValueError("unerwartetes Format")
        result: dict[str, Account] = {}
        for item in raw["accounts"]:
            data = {k: v for k, v in item.items() if k in _FIELD_NAMES}
            acc = Account(**data)
            result[acc.id] = acc
        return result

    def _load(self) -> None:
        if not self.path.exists() and not self.backup_path.exists():
            return
        errors = []
        for candidate in (self.path, self.backup_path):
            if not candidate.exists():
                continue
            try:
                self._accounts = self._read_file(candidate)
                if candidate is self.backup_path:
                    # Hauptdatei war defekt: sichern, nicht überschreiben
                    if self.path.exists():
                        stamp = time.strftime("%Y%m%d-%H%M%S")
                        self.path.replace(self.data_dir / f"accounts.json.corrupt-{stamp}")
                    self._dirty = True
                    self._flush()
                return
            except (OSError, ValueError, TypeError, KeyError) as exc:
                errors.append(f"{candidate.name}: {exc}")
        raise StoreCorruptError(
            "Account-Datei konnte nicht gelesen werden (" + "; ".join(errors) + "). "
            f"Dateien liegen unverändert in {self.data_dir}."
        )

    def _flush(self) -> None:
        payload = {
            "version": SCHEMA_VERSION,
            "accounts": [asdict(a) for a in self._accounts.values()],
        }
        fd, tmp_name = tempfile.mkstemp(dir=self.data_dir, prefix=".accounts-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            if self.path.exists():
                shutil.copy2(self.path, self.backup_path)
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        self._dirty = False

    def _save(self) -> None:
        self._dirty = True
        if self._batch_depth == 0:
            self._flush()

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Mehrere Änderungen mit nur einem Schreibvorgang (z. B. beim Import)."""
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0 and self._dirty:
                self._flush()

    # ------------------------------------------------------------ Abfragen
    def __len__(self) -> int:
        return len(self._accounts)

    def get(self, account_id: str) -> Optional[Account]:
        return self._accounts.get(account_id)

    def find_by_user_id(self, user_id: int) -> Optional[Account]:
        if not user_id:
            return None
        for acc in self._accounts.values():
            if acc.user_id == user_id:
                return acc
        return None

    def view(self, search: str = "", sort: str = "recent", favorites_first: bool = True) -> list[Account]:
        if sort not in SORT_MODES:
            raise ValueError(f"unbekannte Sortierung: {sort}")
        needle = search.strip().casefold()
        items = [a for a in self._accounts.values() if not needle or needle in a.search_key]
        if sort == "alpha":
            items.sort(key=lambda a: a.title.casefold())
        elif sort == "recent":
            items.sort(key=lambda a: (-a.last_used, a.title.casefold()))
        else:
            items.sort(key=lambda a: (-a.created, a.title.casefold()))
        if favorites_first:
            items.sort(key=lambda a: not a.favorite)  # stabil: Reihenfolge bleibt erhalten
        return items

    def recent(self, limit: int = 5) -> list[Account]:
        used = [a for a in self._accounts.values() if a.last_used > 0]
        used.sort(key=lambda a: -a.last_used)
        return used[:limit]

    # ------------------------------------------------------------ Änderungen
    def profile_dir(self, account_id: str) -> Path:
        return self.profiles_dir / account_id

    def add(self, name: str = "") -> Account:
        acc = Account(id=uuid.uuid4().hex, name=name.strip(), created=time.time())
        self._accounts[acc.id] = acc
        self.profile_dir(acc.id).mkdir(parents=True, exist_ok=True)
        self._save()
        return acc

    def rename(self, account_id: str, name: str) -> None:
        self._require(account_id).name = name.strip()
        self._save()

    def set_favorite(self, account_id: str, favorite: bool) -> None:
        self._require(account_id).favorite = bool(favorite)
        self._save()

    def mark_used(self, account_id: str) -> None:
        self._require(account_id).last_used = time.time()
        self._save()

    def set_avatar(self, account_id: str, filename: str) -> None:
        self._require(account_id).avatar = filename
        self._save()

    def set_custom_avatar(self, account_id: str, source: Path) -> str:
        """Kopiert ein eigenes Profilbild in den Avatar-Ordner."""
        acc = self._require(account_id)
        suffix = Path(source).suffix.lower() or ".png"
        target_name = f"{acc.id}-custom{suffix}"
        shutil.copyfile(source, self.avatars_dir / target_name)
        acc.avatar = target_name
        self._save()
        return target_name

    def set_identity(self, account_id: str, username: str, user_id: int) -> None:
        """Übernimmt das Ergebnis einer ECHTEN, von Roblox bestätigten Anmeldung.

        Wirft DuplicateAccountError, wenn dieselbe Roblox-ID schon unter einem
        anderen Eintrag gespeichert ist - es wird nichts überschrieben.
        """
        acc = self._require(account_id)
        other = self.find_by_user_id(user_id)
        if other is not None and other.id != acc.id:
            raise DuplicateAccountError(other)
        acc.username = username
        acc.user_id = int(user_id)
        if not acc.name:
            acc.name = username
        self._save()

    def remove(self, account_id: str, delete_profile: bool = True) -> None:
        """Entfernt genau diesen einen Account (und nur sein eigenes Profil)."""
        acc = self._accounts.pop(account_id, None)
        if acc is None:
            return
        self._save()
        if delete_profile:
            target = self.profile_dir(account_id).resolve()
            if target.parent == self.profiles_dir.resolve() and target.exists():
                shutil.rmtree(target, ignore_errors=True)
        if acc.avatar:
            for f in self.avatars_dir.glob(f"{acc.id}*"):
                try:
                    f.unlink()
                except OSError:
                    pass

    def _require(self, account_id: str) -> Account:
        try:
            return self._accounts[account_id]
        except KeyError:
            raise StoreError(f"Account nicht gefunden: {account_id}") from None
