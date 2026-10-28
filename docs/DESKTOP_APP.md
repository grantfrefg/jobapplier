# The desktop app

A double-click app for Windows and Mac, so friends can use ApplyPilot without installing Python or using a terminal. It's the same web UI, in **friend mode**:

- **You submit, by default.** Forms are filled in and the user clicks Submit. Claude Code or Codex (which submit by themselves) can be chosen in Setup after a confirmation; the app only honours an engine confirmed there (`AGENT_ENGINE_CONFIRMED`), not one left in a copied `.env`.
- **Simpler Setup page.** AI key (Gemini free, or the user's own OpenAI / Anthropic key), browser, resume, job search and profile.
- **Finds installed tools.** A double-clicked app doesn't get the terminal's PATH, so it also looks for `claude`, `codex` and `npx` in their usual install folders (npm, Homebrew, `~/.local/bin`, nvm; `%APPDATA%\npm` on Windows).
- **Starts and stops itself.** Double-clicking opens the page; opening it again just reopens the page. **Quit** in the page closes the app, and it also exits after 30 idle minutes.
- **Usually no browser download.** It uses Chrome or Edge if installed (every Windows PC has Edge). Otherwise **Install browser** in Setup downloads Chromium into `~/.applypilot/browsers`.
- **Data stays on the user's computer**, in `~/.applypilot`.

## Building

GitHub Actions → **Desktop app** → **Run workflow**. After about 15 minutes, download the zips from the run's **Artifacts**:

| Zip | For |
|---|---|
| `ApplyPilot-Windows.zip` | Windows 10/11 |
| `ApplyPilot-Mac-AppleSilicon.zip` | Macs with M-series chips |
| `ApplyPilot-Mac-Intel.zip` | Older Intel Macs |

Each build:
1. runs the unit tests;
2. builds the app with PyInstaller (`packaging/ApplyPilot.spec`);
3. **smoke-tests the packaged app** (`packaging/smoke_test.py`): CLI mode, desktop mode, a pipeline stage run from the app, and Quit;
4. zips it with a `READ_ME_FIRST` file.

Push a version tag (e.g. `git tag v0.5.0 && git push origin v0.5.0`) to also attach the zips to a GitHub Release.

### Building locally
```bash
pip install ".[ui]" -r packaging/requirements-desktop.txt
cd packaging && pyinstaller ApplyPilot.spec --noconfirm
python smoke_test.py dist/ApplyPilot/ApplyPilot      # Windows: dist/ApplyPilot/ApplyPilot.exe
                                                     # Mac: dist/ApplyPilot.app/Contents/MacOS/ApplyPilot
```

## Sharing while the repo is private

Friends can't open a private repo's Releases or Artifacts. Download the zips yourself and share them through Google Drive, Dropbox or similar.

## First-open warnings

The app isn't code-signed, so the first launch shows a warning. Each zip's `READ_ME_FIRST` explains the click-through:
- **Windows:** "Windows protected your PC" → **More info** → **Run anyway**.
- **Mac:** "can't verify the app" → **System Settings → Privacy & Security → Open Anyway**.

Removing the warnings needs a Windows code-signing certificate and an Apple Developer account (about $99/year), and the build would then need to sign and notarise the app.

## How it fits together

| Piece | Role |
|---|---|
| `packaging/desktop_entry.py` | No arguments → start the app. With arguments → behave like the `applypilot` CLI; the UI runs pipeline stages this way |
| `src/applypilot/desktop.py` | Picks a free port (8000–8010), starts the web server, opens the browser, makes sure only one copy runs, and exits when idle |
| `APPLYPILOT_DESKTOP=1` | Friend mode: starts on the local engine (Claude Code / Codex only once confirmed in Setup), extends PATH for the agent CLIs, hides developer settings |
| `PLAYWRIGHT_BROWSERS_PATH` | Set to `~/.applypilot/browsers`, so a downloaded browser never goes inside the signed app bundle |

## Notes

- The download is roughly 150–250 MB, mostly Python, the job-board libraries and Playwright's Node runtime.
- macOS builds use about 10× the GitHub Actions minutes on private repos. One full build is roughly 300–400 of the 2,000 free monthly minutes.
- **Licence (AGPL-3.0):** if you give someone the app, you must also offer them the source code.
