# .exe automatisch bauen lassen (GitHub, ohne Python bei dir)

1. Auf github.com ein neues **privates** Repository anlegen (z. B. `roblox-account-switcher`).
2. Den entpackten Ordner-Inhalt hochladen (**Add file → Upload files**, auch den versteckten
   Ordner `.github` mit hochladen – am einfachsten alles per Drag & Drop in den Browser ziehen).
3. Der Bau startet jetzt **automatisch** bei jedem Hochladen. Die fertige Datei findest du unter
   **Releases** (rechts auf der Repo-Startseite). Manuell geht es auch: oben auf **Actions** → links **„Windows-exe bauen“** → **Run workflow**.
4. Nach ein paar Minuten (grüner Haken) das Ergebnis unter **Artifacts** herunterladen:
   `RobloxAccountSwitcher-windows.zip`.
5. Zip entpacken und `RobloxAccountSwitcher.exe` im entpackten Ordner starten.
   Den **ganzen Ordner** zusammenlassen, die .exe braucht die Dateien daneben.

Hinweise
- Die Tests laufen vor dem Bau mit. Schlägt etwas fehl, gibt es keine .exe.
- Windows SmartScreen kann bei einer selbst gebauten .exe warnen („Weitere Informationen →
  Trotzdem ausführen“). Das ist normal, weil die Datei nicht signiert ist.
- Die .exe-Erstellung konnte ich hier nicht selbst ausführen (kein Windows). Der Ablauf ist
  Standard (PyInstaller + PySide6). Wenn der Bau fehlschlägt, schick mir das Protokoll aus
  dem roten Schritt, dann behebe ich es.
