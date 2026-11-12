# Development & Packaging

[中文](开发与打包.md) | **English**

For people modifying the code or building it themselves. Conventions and pitfalls are in
[AGENTS.md](../AGENTS.md).

## Environment

Python 3.9+ with the bundled virtualenv `.venv`:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m heartapp --simulate    # simulated data, no watch needed
```

Three dependencies only: `PySide6-Essentials` (UI), `bleak` (BLE, pulls in `winrt-*` on
Windows) and `websockets` (rooms).

## Tests

```powershell
.venv\Scripts\python.exe run_tests.py                       # unit tests (187 today)
.venv\Scripts\python.exe smoke_test.py                      # backend end-to-end
.venv\Scripts\python.exe tools\qt_smoke_test.py             # UI flow (automated)
.venv\Scripts\python.exe tools\room_smoke_test.py           # two-client rooms
.venv\Scripts\python.exe tools\server_panel_smoke_test.py   # server panel
```

Run the first three before committing; add the fourth for room changes and the fifth for
panel changes.

Screenshots can be regenerated (synthetic data only, never committed):
`tools\d_gui_preview.py`, `tools\room_comparison_preview.py`, `tools\lobby_preview.py`.

## Building

```powershell
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe tools\build_exe.py
```

Four artifacts (signed automatically):

```
dist\心率记录仪.exe                     onefile · GUI
dist\心率记录仪-命令行.exe               onefile · CLI
dist\心率记录仪-便携版\图形界面版\...      onedir · GUI  (recommended distribution)
dist\心率记录仪-便携版\命令行版\...        onedir · CLI
```

**Two hard constraints — do not change them casually:**

1. **The onefile build must keep `--runtime-tmpdir .`**
   By default PyInstaller extracts into the system temp directory, which fails on some
   machines (`Could not create temporary directory!`, before any Python runs, so not even a
   log is produced). A hello-world onefile reproduced this locally. The onedir build needs
   no extraction, which is why it is the recommended download.
2. **Exclude `tkinter` in the GUI build, plus `PySide6`/`shiboken6` in the CLI build**
   otherwise the CLI artifact is also ~37 MB.

## Releasing

**One release per version — client and server together:**

```powershell
# version lives in heartapp/__init__.py; notes in docs/release-notes/v<version>.md
.venv\Scripts\python.exe tools\build_release.py
git tag -a v1.4.0 -m "..." && git push origin main v1.4.0
gh release create v1.4.0 --title "..." --notes-file "docs/release-notes/v1.4.0.md" dist\release\*
```

`build_release.py` runs all three build scripts, signs everything, assembles `dist\release\`
and writes one `SHA256SUMS.txt` covering: 3 client + 3 server + 1 server-panel artifact.

Notes:

- Asset names must be **ASCII** (`gh` truncates Chinese names to something like `-.exe`)
- **Sign before zipping**, otherwise the archives contain unsigned executables
- `gh release upload --clobber` **deletes then uploads**; a timeout on a large file leaves the
  asset missing — upload one file at a time with retries

## Code signing

Self-signed certificate; once trusted locally the publisher shows as `HeartApp`.

| Item | Detail |
|---|---|
| Certificate | `CN=HeartApp`, RSA 2048, 10 years |
| Private key | **Non-exportable** |
| Trust scope | **This machine only** |
| SmartScreen | May still warn (based on reputation, not signature) |

The build scripts sign automatically; use `tools/bat/给exe签名.bat` manually and
`移除签名证书.bat` to revoke. Commercial certificates: see [signing notes](签名说明.md).

## Project layout

```
heartapp/                     ★ main package
├─ hr.py           0x2A37 frame parsing + data model
├─ engine.py       capture engine: asyncio worker thread + event queue
├─ store.py        SQLite storage (with recording process lock)
├─ paths.py        path resolution (data always next to the exe)
├─ cli.py          command line
├─ relay.py        server entry        lobby.py       login / rooms / seats
├─ room_protocol.py protocol (pure)    room_client.py client network thread
└─ ui/qt/          ★ Qt UI
   ├─ editorial.py / editorial_layout.py  design tokens + three-page layout
   ├─ chrome.py    frameless window     app.py         main window + event pump
   └─ room_panel.py / lobby_panel.py / comparison.py  rooms UI
packaging/        PyInstaller entry points
server/           deployment (Docker / nginx / systemd / management CLI)
tools/            build, sign, test, screenshot scripts (bat/ holds maintenance .bat)
tests/            unit tests
docs/             guides, signing, design, release notes
```

**Layering rule**: business logic (hr / metrics / store / engine) must never import UI modules.

> `.bat` files must use CRLF; `.ps1` files must be UTF-8 with BOM. Details in AGENTS.md.
