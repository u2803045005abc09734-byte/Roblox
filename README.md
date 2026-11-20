# Roblox Account Switcher

Ein Launcher-Fenster mit **unbegrenzt vielen** Roblox-Accounts, Ein-Klick-Wechsel,
Favoriten, Suche, Sortierung und „Zuletzt verwendet“.

## Starten
- Windows: Doppelklick auf `start.bat` (installiert beim ersten Mal PySide6)
- Linux/macOS: `./start.sh`
- Oder: `pip install -r requirements.txt` und `python app.py` (Python 3.10+)

**Als .exe:** siehe `EXE-ERSTELLEN.md` (GitHub baut sie automatisch, du brauchst kein Python).

## Bedienung
1. **+ Account** → Name vergeben → im neuen Fenster **ganz normal bei Roblox anmelden**
   (Passwort, 2FA, Captcha auf der offiziellen Seite). Einmal reicht.
2. **Ein Klick auf einen Account** öffnet sein Fenster. Dort auf der Roblox-Seite ein Spiel
   öffnen und **Spielen** drücken – der Roblox Player startet mit genau diesem Account.
3. **AKTIV** (grüner Rahmen) steht nur bei dem Account, dessen Anmeldung Roblox gerade
   tatsächlich bestätigt hat. Daneben zeigt jeder Eintrag seinen echten Status:
   *Angemeldet*, *Nicht angemeldet*, *Prüfe …*, *Status unklar*, *Nicht geprüft*.
4. Stern = Favorit · Rechtsklick = Umbenennen, eigenes Profilbild, Status prüfen, Entfernen.
5. **Alle prüfen** testet jeden gespeicherten Account im Hintergrund (3 gleichzeitig).

## Wie es sicher funktioniert
- Jeder Account hat sein **eigenes, getrenntes Browser-Profil** (QtWebEngine). Roblox’ eigene
  Sitzung bleibt in diesem Profil – dieses Programm **liest, kopiert und speichert keine
  Cookies, Passwörter oder Tokens** und umgeht keine Roblox-Sicherheitsmechanismen.
- Der Status kommt von der offiziellen Adresse `users.roblox.com/v1/users/authenticated`,
  die in einem unsichtbaren Fenster im jeweiligen Profil geladen wird.
- In `accounts.json` stehen nur: Anzeigename, Roblox-Name/-ID, Avatar-Dateiname, Favorit,
  Zeitstempel. Schreiben ist atomar, mit `.bak`-Sicherung; eine beschädigte Datei wird nie
  still überschrieben.
- Wird dieselbe Roblox-ID in zwei Einträgen erkannt, wird nichts überschrieben – du bekommst eine Meldung.
- Entfernen löscht nur das Profil dieses einen Eintrags. Dein Roblox-Account bleibt unberührt.

Daten liegen unter `%APPDATA%\RobloxAccountSwitcher` (Windows) bzw.
`~/.local/share/RobloxAccountSwitcher`. Ordner `profiles/` = Sitzungen, `avatars/` = Bilder.
Wer den Ordner kopiert, kopiert damit auch die angemeldeten Sitzungen – wie ein Browser-Profil
behandeln und nicht weitergeben.

## Grenzen (ehrlich)
- Roblox bietet **keine offizielle Schnittstelle**, um ohne Anmeldung zwischen Accounts zu wechseln.
  Deshalb ist die einmalige Anmeldung pro Account nötig; danach ist der Wechsel ein Klick.
  Läuft eine Sitzung ab oder verlangt Roblox eine neue Bestätigung, zeigt der Eintrag
  *Nicht angemeldet* und du meldest dich im geöffneten Fenster neu an.
- Mehrere Roblox-Player-Fenster gleichzeitig (Multi-Instance) sind **nicht** enthalten.
- Die Liste nutzt eine virtualisierte Ansicht und bleibt auch bei Tausenden Einträgen flüssig
  (getestet mit 5000 Einträgen im Speicher und 500 in der Oberfläche).

## Tests
```
python -m unittest discover -s tests          # Speicher + Status-Auswertung
python tests/smoke_gui.py out.png             # Oberfläche mit 500 Accounts, Klicks, Screenshot
python tests/run_session_isolation.py all     # echte Profil-Isolation (lokaler Testserver)
```
