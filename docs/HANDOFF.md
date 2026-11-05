# Handoff: Jobapplier (as of v0.12.0, 2026-11-05)

This is for a fresh session that starts with a review of the codebase. It covers what the
app is, how work is done and released, where things are, and what to look at first.

## What it is

Jobapplier is a desktop app that finds jobs, scores how well they fit, writes a tailored
resume/CV and cover letter for each one, and fills in application forms in a visible
browser. The app never presses Submit; the user does. It was built for Australia and also
supports the UK (London). It started as a fork of ApplyPilot (AGPL-3.0). The credit stays in
README, CHANGELOG, CONTRIBUTING and the Guide footer.

- Repo: `grantfrefg/jobapplier`. Default branch `master`. Latest release **v0.12.0**, with
  Windows, Mac Apple Silicon and Mac Intel zips attached.
- Users run the desktop app, a PyInstaller bundle of a FastAPI server plus a single-page UI
  that opens in a window or browser. A Typer CLI (`applypilot` / `jobapplier`) drives the
  same pipeline.
- The user-visible name is "Jobapplier". Internal names stay `applypilot` on purpose: the
  Python package, the `~/.applypilot` data folder (`APPLYPILOT_DIR` overrides it), the env
  var names and the macOS bundle id `com.applypilot.desktop`. Renaming them would break
  existing users' data and settings.

## How we work and release (keep doing this)

1. Develop on branch `claude/applypilot-local-playwright-pvd8z2`. Reset it to `origin/master`
   before new work, because PRs are squash-merged. If a push is rejected afterwards, run
   `git merge -s ours origin/<branch>` instead of force-pushing.
2. Before pushing, run:
   - `pytest tests` (~380 pass, ~18 skip without a display);
   - `ruff check --select E,F,W src packaging tests` (the same as CI's Lint step; all of
     `src` is clean since 0.13.0, with long prompt/HTML lines allowed per file in `pyproject.toml`).
3. Bump the version in **three places**: `pyproject.toml`, `src/applypilot/__init__.py`, and
   `packaging/Jobapplier.spec` (CFBundleShortVersionString). Add a `CHANGELOG.md` entry.
4. Open a PR to master, then run the `desktop.yml` workflow on the branch so the
   Windows/Mac builds run. Merge only when CI (Python 3.11–3.13) and all three builds are green.
5. Tell the user "master is ready to publish vX". **The user publishes the release in the
   GitHub UI.** Never publish a release before the merge; that put old code under v0.6.1 once.
   - Publishing the tag runs `desktop.yml`, which attaches the zips.
   - After the user publishes, check the zips are attached.
   - A manual run with input `release_tag` attaches zips to an existing release.
6. Commits end with the Co-Authored-By / Claude-Session lines. No model names in commits or PRs.
7. User-facing text uses plain words, Australian spelling, and "resume" (AU) or "CV" (UK).

## Map of the code (`src/applypilot/`)

| Area | Files | Notes |
|---|---|---|
| UI | `ui/server.py` (~1150 lines), `ui/static/index.html` (~960 lines of HTML, incl. the Guide), `app.js` (~1040), `app.css`; only those two are served from `/static/` | Tabs: Setup, Find jobs, Apply, Guide. `/api/jobs` builds the Apply list. |
| Desktop | `desktop.py`, `packaging/Jobapplier.spec`, `packaging/desktop_entry.py`, `smoke_test.py` | |
| Config and data | `config.py`, `database.py` (SQLite `jobs` table; missing columns added by ALTER TABLE at start-up), `profile_options.py` | Settings live in `~/.applypilot/.env`, profile.json, searches.yaml |
| Market | `market.py` | AU/UK `Market` dataclass. `market.localise(text, swaps, mk)` swaps AU passages for UK ones and **raises if a passage is missing**, so AU prompts stay unchanged. |
| Discovery | `discovery/jobspy.py` (Indeed/LinkedIn via JobSpy), `discovery/reed.py` (Reed API, `REED_API_KEY`), `discovery/smartextract.py` + `config/sites.yaml` (page-read boards), `discovery/workday.py`, `enrichment/detail.py` (full descriptions) | `pipeline.py` runs the stages |
| Scoring | `scoring/scorer.py` | Several jobs per AI request, JSON replies. `SCORE_PROMPT` + `UK_SWAPS`. |
| Writing | `scoring/tailor.py` (resume + letter in one request), `scoring/cover_letter.py`, `scoring/context.py` (candidate brief, house style), `scoring/voice.py` ("Reads human"), `stories.py` (story bank) | |
| Checks | `scoring/facts.py` (code fact-check: titles, employers, numbers, `TECH_TERMS` tools incl. marketing), `scoring/validator.py`, `scoring/ats.py` (ATS score), plus an AI judge in tailor.py | |
| Documents | `scoring/pdf.py` (HTML→PDF via Playwright), `scoring/look.py` (layouts, accent, work-rights display, page target, `job_kind`, `short_term_role`), `scoring/style_match.py` (match my PDF), `scoring/docx_fill.py` (fill my Word file), `own_docs.py` | |
| Applying | `apply/local_engine.py` (current form filler, ~1500 lines), `apply/answers.py` (saved answers), `apply/adapters.py`, `apply/sites.py`, `apply/login_browser.py`, `apply/prompt.py` | `apply/launcher.py`, `apply/chrome.py`, `apply/dashboard.py` are older upstream code (Claude-CLI based). The CLI still imports parts of them. |
| LLM | `llm.py`, `llm_anthropic.py`, `llm_cli.py`, `budget.py` (daily request budget) | |
| Other | `applicants.py` (applicant counts), `rerun.py`, `wizard/init.py`, `cli.py`, `view.py` | |

Docs: `docs/HOW_IT_WORKS.md`, `docs/CONFIGURATION.md`, `docs/DESKTOP_APP.md`,
`docs/IMPROVEMENTS.md` (AI-request reduction plan), `docs/UK_PLAN.md` (built in 0.11.0).

## What was built recently (0.6 → 0.12)

- Fact-check fixes: false "fabricated skill" flags (C++, Vue, Spring), employer header
  matching, and extra profile skills kept out of roles.
- Resume look: Modern/Classic/Compact/Traditional layouts, accent colour, tagline, tech line,
  work-rights display (auto/always/never), length (auto/1/2 pages, 1 for graduates).
  - "Match my resume" (from the user's PDF) and "My Word file" (fills their .docx).
  - Live layout pictures (0.11.1).
- Story bank; contract/temp job filter in searches; working holiday / Youth Mobility visa
  shown per job for short-term roles only.
- Rename to Jobapplier (0.10.0); applicant counts per job.
- UK/London support (0.11.0): market module, British spelling and CV wording, £ and pension
  rules, UK work rights, London areas, Reed API, Hays UK and Randstad UK.
- 0.12.0: job type shown, filtered and sorted in Apply jobs (`look.job_kind`, read from ad
  text). Scoring, CV and letter prompts now work for marketing and other non-tech fields.

## Known gaps and things to check in the review

- **Big files**: `local_engine.py`, `smartextract.py` and `ui/server.py` are candidates for
  splitting. The UI's JS and CSS moved out of `index.html` in 0.13.0; `app.js` could be split by tab next.
- **Legacy upstream code**: `apply/launcher.py`, `chrome.py`, `prompt.py` and `dashboard.py`
  are still live: they run the Claude Code and Codex engines, and `launcher` holds the job
  queue the UI uses. They're linted since 0.13.0.
- **Prompt duplication**: UK versions are made by exact-text swaps. That's safe because it
  raises on a missing passage and is tested, but editing it is fragile. `tests/test_market.py`
  covers it.
- **Job type** is a regex guess from ad text (`look.job_kind`); JobSpy's own `job_type` field
  isn't stored. `short_term_role` and `job_kind` overlap.
- **Untested live**:
  - LinkedIn applicant counts and LinkedIn UK (the cloud gets HTTP 429).
  - Reed (mock only).
  - Find a job (DWP) was down when tested.
  - Boards that block automated browsers aren't used: CV-Library, Jora UK, Adzuna,
    Robert Walters, CareerOne, Programmed.
- **Marketing support** is prompt and vocabulary only; it hasn't been tried on a real
  marketing CV.
- **Security/privacy surface**: the local server has no authentication. It binds to
  127.0.0.1, and since 0.12.1 a middleware (`_refusal` in `ui/server.py`) refuses unknown Host
  names (DNS rebinding) and cross-site POSTs (Origin / Sec-Fetch-Site). Keep that when adding
  endpoints. API keys are in `.env` (mode 600 on POSIX). File upload endpoints for own documents
  and resumes.
- **Tests**: `tests/` (32 files). Conftest isolates env keys with `setenv(key, "")`. UI tests
  use FastAPI's TestClient. Browser tests need a display (xvfb in CI).

## Handy commands

```bash
pip install -e ".[dev,ui]"         # see CONTRIBUTING.md for the full setup
pytest tests -q
APPLYPILOT_DIR=/tmp/somewhere python -c "from applypilot.ui.server import serve; serve(port=8798)"  # UI with throwaway data
```
