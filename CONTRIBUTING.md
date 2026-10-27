# Contributing

## Setup

**Dev container (recommended, especially on Windows):** open the repo in VS Code and choose **Reopen in Container**. Python, all dependencies, Chromium and a virtual desktop for the visible browser are installed automatically, and the web UI starts at http://localhost:8000. See the README for details.

**Local install:**
```bash
pip install -e ".[dev,ui]" -r packaging/requirements-desktop.txt
python -m playwright install chromium
applypilot doctor
```

## Workflow

1. Start a branch from the latest `master`, named like `feature/…`, `fix/…`, `docs/…` or `ci/…`.
2. Make the change with tests. Bug fixes get a regression test that fails without the fix.
3. Run the checks below locally.
4. Push and open a pull request into `master`. Say what changed, why, and how you tested it.
5. CI (`.github/workflows/ci.yml`) runs on every PR: the full test suite on Python 3.11, 3.12 and 3.13, plus lint. Merge once it's green.

Don't push directly to `master`.

## Checks

```bash
xvfb-run -a pytest tests      # full suite; xvfb gives the visible-browser tests a display
pytest tests                  # without a display, the browser tests are skipped

ruff check --select E,F,W \
  src/applypilot/ui src/applypilot/desktop.py src/applypilot/__main__.py \
  src/applypilot/apply/local_engine.py src/applypilot/database.py \
  packaging tests
```

Lint covers only the modules that are already clean. Older upstream modules still have findings; if you clean one up, add it to the lint step in `ci.yml`.

Tests never call real AI services or job sites. They use fake LLM clients, `httpx.MockTransport`, and local HTML forms.

## Desktop app

Changes to `desktop.py`, `ui/`, `packaging/` or anything imported at startup can break the packaged app in ways the unit tests don't catch. Before merging such a change, run **Actions → Desktop app → Run workflow**: it builds and smoke-tests Windows and both Mac versions. See [docs/DESKTOP_APP.md](docs/DESKTOP_APP.md).

## Project layout

```
src/applypilot/
  cli.py              # `applypilot` commands
  config.py           # paths, .env, engine/model settings, location filter, browser detection
  database.py         # SQLite schema, migrations, stats, cross-site de-duplication, Redo marks
  llm.py              # LLM client (Gemini / OpenAI / local), JSON mode, readable errors
  budget.py           # daily Gemini request budget
  profile_options.py  # fixed answers for profile drop-downs (and mapping of old wording)
  pipeline.py         # runs stages in order or streaming
  desktop.py          # double-click app launcher (friend mode)
  discovery/          # jobspy.py (Indeed/LinkedIn), smartextract.py (Jora etc.), workday.py
  enrichment/         # detail.py: full descriptions and application links
  scoring/            # scorer.py, tailor.py, cover_letter.py, pdf.py
                      # facts.py: generated text vs the real resume (titles, skills, tools...)
                      # validator.py: banned words, AI phrasing, Australian spelling, structure
                      # ats.py: ATS check; context.py: candidate facts and house style
  apply/              # launcher.py (queue + engine switch), local_engine.py (free engine),
                      # answers.py (standard fields, answer bank), adapters.py (SEEK, Lever),
                      # sites.py (where a job applies), login_browser.py (normal login window),
                      # chrome.py/prompt.py/dashboard.py (Claude Code engine)
  ui/                 # server.py (FastAPI) + static/index.html (the web UI)
  wizard/             # `applypilot init`, shared search-config builder
  config/             # bundled sites.yaml, employers.yaml, searches.example.yaml
packaging/            # PyInstaller spec, desktop entry point, smoke test, READ_ME_FIRST files
.devcontainer/        # dev container (Python + virtual desktop, auto-starts the UI)
docs/                 # how it works, configuration, desktop app, improvement plan
tests/
```

## Conventions

- **Match the code around you:** type hints and docstrings on public functions, a 120-character line limit, and comments that say *why*.
- **Settings:** user settings go in `~/.applypilot/` (`.env`, `searches.yaml`, `profile.json`). Defaults live in `config.py`.
- **Privacy:** never send personal identifiers to an AI provider. Use the placeholder approach in `local_engine.py`.
- **Safety:** never let automation click a final Submit / Apply / Send, and never type a password.
- **Honesty:** generated resumes, letters and form answers may only claim what's in the user's resume (or their confirmed skills list). Enforce it in code (`scoring/facts.py`), not only in prompts, and test it with a made-up claim.
- **Australia first:** Australian English, A4, DD/MM/YYYY, AUD, and Australian screening questions (work rights, clearance, selection criteria).
- **AI requests:** AI calls cost the user's daily free requests. Prefer batching, structured (JSON) output and free checks before an AI call. See [docs/IMPROVEMENTS.md](docs/IMPROVEMENTS.md).
- **Changelog:** update `CHANGELOG.md` under `[Unreleased]`.

## Licence

AGPL-3.0, inherited from [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot). Contributions are licensed the same way.
