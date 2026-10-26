<!-- logo here -->

> **⚠️ ApplyPilot** is the original open-source project, created by [Pickle-Pixel](https://github.com/Pickle-Pixel) and first published on GitHub on **February 17, 2026**. We are **not affiliated** with applypilot.app, useapplypilot.com, or any other product using the "ApplyPilot" name. These sites are **not associated with this project** and may misrepresent what they offer. If you're looking for the autonomous, open-source job application agent — you're in the right place.

# ApplyPilot

**Applied to 1,000 jobs in 2 days. Fully autonomous. Open source.**

[![PyPI version](https://img.shields.io/pypi/v/applypilot?color=blue)](https://pypi.org/project/applypilot/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/Pickle-Pixel/ApplyPilot?style=social)](https://github.com/Pickle-Pixel/ApplyPilot)
[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/S6S01UL5IO)




https://github.com/user-attachments/assets/7ee3417f-43d4-4245-9952-35df1e77f2df


---

## What It Does

ApplyPilot is a 6-stage autonomous job application pipeline. It discovers jobs across 5+ boards, scores them against your resume with AI, tailors your resume per job, writes cover letters, and **submits applications for you**. It navigates forms, uploads documents, answers screening questions, all hands-free.

Three commands. That's it.

```bash
pip install applypilot
pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex
applypilot init          # one-time setup: resume, profile, preferences, API keys
applypilot doctor        # verify your setup — shows what's installed and what's missing
applypilot run           # discover > enrich > score > tailor > cover letters
applypilot run -w 4      # same but parallel (4 threads for discovery/enrichment)
applypilot apply         # autonomous browser-driven submission
applypilot apply -w 3    # parallel apply (3 Chrome instances)
applypilot apply --dry-run  # fill forms without submitting
```

> **Why two install commands?** `python-jobspy` pins an exact numpy version in its metadata that conflicts with pip's resolver, but works fine at runtime with any modern numpy. The `--no-deps` flag bypasses the resolver; the second command installs jobspy's actual runtime dependencies. Everything except `python-jobspy` installs normally.

---

## Easiest setup: dev container + web UI (no terminal needed)

Recommended on Windows. Everything runs inside a Linux container, so you don't install Python, Playwright or Chromium on your machine.

**You need:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) and [VS Code](https://code.visualstudio.com/) with the **Dev Containers** extension.

1. Open this folder in VS Code and choose **Reopen in Container** when prompted (or `F1` → *Dev Containers: Reopen in Container*). The first build takes a few minutes.
2. Open **http://localhost:8000** in your browser. The ApplyPilot UI starts automatically every time the container starts.
3. Work through the three tabs:
   - **1. Setup:** pick the free or paid engine, paste your Gemini API key, paste your resume text (and upload the PDF), choose job titles and location, and fill in your profile. The checklist at the top turns green as you go.
   - **2. Find jobs:** press **Run**. It discovers jobs, scores them, and writes tailored resumes and cover letters. The log streams on the page.
   - **3. Apply:** press **Apply** next to a job. With the free engine, the live browser appears right on the page and fills in the form. When it stops at **Your turn: review and submit**, check the form, click Submit inside the live browser yourself, then press **I submitted it**. Other options: **Not yet: save for later**, **Fill this page again** (e.g. after you log in to SEEK), or **Never apply to this job**.

You can also watch the browser on its own at **http://localhost:6080**. Your data (profile, keys, resumes, job database) lives in a Docker volume called `applypilot-data`, so it survives container rebuilds. If VS Code forwards a port to a different number because 8000 or 6080 is busy, use the number shown in the **Ports** panel.

### Testing from the terminal inside the container

In VS Code's terminal (which runs inside the container), every command from this README works as-is. Browser windows show up in the live view at http://localhost:6080:

```bash
applypilot doctor                      # check setup
applypilot init                        # terminal setup wizard (alternative to the UI)
applypilot run                         # find + prepare jobs
EXECUTION_ENGINE=local_playwright applypilot apply --url <job-url>   # one job, free engine
pytest tests/                          # run the test suite
```

### Without a dev container

```bash
pip install -e ".[ui]"
python -m playwright install chromium
applypilot ui        # → http://localhost:8000
```

On a normal desktop, the browser window opens on your screen instead of inside the page.

---

## Job sources in Australia

When the job search country is **Australia** (the default in the setup wizard and the web UI), discovery uses:

- **Indeed Australia** (au.indeed.com) and **LinkedIn**, through JobSpy.
- **Jora** (au.jora.com), through smart extract. This costs about 2 Gemini requests per job title searched.

The bundled North American sources are switched off: ZipRecruiter, the Canadian and US-remote boards in `config/sites.yaml`, and the Canadian/US employers in `config/employers.yaml`. These choices live in `searches.yaml` as `sites`, `smart_extract_sites` and `workday_employers`; delete a key to use every bundled source again. Jobs are kept if their location matches `location_accept` (generated from the city you enter, e.g. *Sydney, NSW, New South Wales*) or they're remote.

**Not included:**
- **SEEK:** its terms prohibit scraping and it has strong bot protection. Applying to SEEK jobs you find yourself works as normal.
- **APS Jobs:** its listings don't render for automated browsers.
- **Glassdoor:** it blocks automated searches (it returned "403 blocked" when tested from a server), and Glassdoor applications are on the blocked list because they route through Glassdoor accounts.

---

## Sharing with friends: the desktop app

The **Desktop app** GitHub Actions workflow builds a double-click app for Windows and Mac (Apple Silicon and Intel). Each build runs the tests and smoke-tests the packaged app before uploading it. The desktop app is the same web UI in **friend mode**:

- **Free engine only:** forms are filled in, and your friend clicks Submit.
- **Simpler Setup page:** just a Gemini key, the browser, resume, job search and profile.
- **No terminal:** double-click opens the page, and **Quit** in the page closes the app. It also exits by itself after 30 idle minutes.
- **No extra browser download on most machines:** it uses Chrome or Edge if installed (every Windows PC has Edge). Otherwise **Install browser** on the Setup page downloads one into `~/.applypilot/browsers`.
- **Each friend's data stays on their own computer**, in `~/.applypilot`.

**To build it:** go to **Actions → Desktop app → Run workflow**. After about 15 minutes, download the zips from the run's **Artifacts** section. Pushing a tag such as `v0.4.0` also attaches them to a GitHub Release.

**To share it while the repo is private:** your friends can't open private Releases or Artifacts. Download the zips yourself and share them by Google Drive, Dropbox or similar. Each zip has a `READ_ME_FIRST` file covering the one-time "unverified app" warning on Windows and Mac.

**Notes**
- The app isn't code-signed. Windows shows "Windows protected your PC" (fix: *More info → Run anyway*). On Mac, use *System Settings → Privacy & Security → Open Anyway*. Removing these warnings needs a paid Windows code-signing certificate and an Apple Developer account ($99/yr).
- Private repos use GitHub Actions minutes, and macOS minutes count 10×. One full build costs roughly 300–400 of the 2,000 free monthly minutes.
- ApplyPilot is AGPL-3.0. If you give someone the app, you must also offer them the source code: a link to this repo (once public) or a zip of it.
- To build locally: `pip install ".[ui]" -r packaging/requirements-desktop.txt`, then `cd packaging && pyinstaller ApplyPilot.spec`. Check the result with `python packaging/smoke_test.py <path to the built executable>`.

---

## Two Paths

### Full Pipeline (recommended)
**Requires:** Python 3.11+, Node.js (for npx), Gemini API key (free), Claude Code CLI, Chrome

Runs all 6 stages, from job discovery to autonomous application submission. This is the full power of ApplyPilot.

### Discovery + Tailoring Only
**Requires:** Python 3.11+, Gemini API key (free)

Runs stages 1-5: discovers jobs, scores them, tailors your resume, generates cover letters. You submit applications manually with the AI-prepared materials.

---

## The Pipeline

| Stage | What Happens |
|-------|-------------|
| **1. Discover** | Scrapes 5 job boards (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs) + 48 Workday employer portals + 30 direct career sites |
| **2. Enrich** | Fetches full job descriptions via JSON-LD, CSS selectors, or AI-powered extraction |
| **3. Score** | AI rates every job 1-10 based on your resume and preferences. Only high-fit jobs proceed |
| **4. Tailor** | AI rewrites your resume per job: reorganizes, emphasizes relevant experience, adds keywords. Never fabricates |
| **5. Cover Letter** | AI generates a targeted cover letter per job |
| **6. Auto-Apply** | Claude Code navigates application forms, fills fields, uploads documents, answers questions, and submits |

Each stage is independent. Run them all or pick what you need.

---

## ApplyPilot vs The Alternatives

| Feature | ApplyPilot | AIHawk | Manual |
|---------|-----------|--------|--------|
| Job discovery | 5 boards + Workday + direct sites | LinkedIn only | One board at a time |
| AI scoring | 1-10 fit score per job | Basic filtering | Your gut feeling |
| Resume tailoring | Per-job AI rewrite | Template-based | Hours per application |
| Auto-apply | Full form navigation + submission | LinkedIn Easy Apply only | Click, type, repeat |
| Supported sites | Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs, 46 Workday portals, 28 direct sites | LinkedIn | Whatever you open |
| License | AGPL-3.0 | MIT | N/A |

---

## Requirements

| Component | Required For | Details |
|-----------|-------------|---------|
| Python 3.11+ | Everything | Core runtime |
| Node.js 18+ | Auto-apply | Needed for `npx` to run Playwright MCP server |
| Gemini API key | Scoring, tailoring, cover letters | Free tier (15 RPM / 1M tokens/day) is enough |
| Chrome/Chromium | Auto-apply | Auto-detected on most systems |
| Claude Code CLI | Auto-apply | Install from [claude.ai/code](https://claude.ai/code) |

**Gemini API key is free.** Get one at [aistudio.google.com](https://aistudio.google.com). OpenAI and local models (Ollama/llama.cpp) are also supported.

### Optional

| Component | What It Does |
|-----------|-------------|
| CapSolver API key | Solves CAPTCHAs during auto-apply (hCaptcha, reCAPTCHA, Turnstile, FunCaptcha). Without it, CAPTCHA-blocked applications just fail gracefully |

> **Note:** python-jobspy is installed separately with `--no-deps` because it pins an exact numpy version in its metadata that conflicts with pip's resolver. It works fine with modern numpy at runtime.

---

## Configuration

All generated by `applypilot init`:

### `profile.json`
Your personal data in one structured file: contact info, work authorization, compensation, experience, skills, resume facts (preserved during tailoring), and EEO defaults. Powers scoring, tailoring, and form auto-fill.

### `searches.yaml`
Job search queries, target titles, locations, boards. Run multiple searches with different parameters.

### `.env`
API keys and runtime config: `GEMINI_API_KEY`, `LLM_MODEL`, `CAPSOLVER_API_KEY` (optional).

### Package configs (shipped with ApplyPilot)
- `config/employers.yaml` - Workday employer registry (48 preconfigured)
- `config/sites.yaml` - Direct career sites (30+), blocked sites, base URLs, manual ATS domains
- `config/searches.example.yaml` - Example search configuration

---

## How Stages Work

### Discover
Queries Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs via JobSpy. Scrapes 48 Workday employer portals (configurable in `employers.yaml`). Hits 30 direct career sites with custom extractors. Deduplicates by URL.

### Enrich
Visits each job URL and extracts the full description. 3-tier cascade: JSON-LD structured data, then CSS selector patterns, then AI-powered extraction for unknown layouts.

### Score
AI scores every job 1-10 against your profile. 9-10 = strong match, 7-8 = good, 5-6 = moderate, 1-4 = skip. Only jobs above your threshold proceed to tailoring.

### Tailor
Generates a custom resume per job: reorders experience, emphasizes relevant skills, incorporates keywords from the job description. Your `resume_facts` (companies, projects, metrics) are preserved exactly. The AI reorganizes but never fabricates.

### Cover Letter
Writes a targeted cover letter per job referencing the specific company, role, and how your experience maps to their requirements.

### Auto-Apply
Claude Code launches a Chrome instance, navigates to each application page, detects the form type, fills personal information and work history, uploads the tailored resume and cover letter, answers screening questions with AI, and submits. A live dashboard shows progress in real-time.

The Playwright MCP server is configured automatically at runtime per worker. No manual MCP setup needed.

```bash
# Utility modes (no Chrome/Claude needed)
applypilot apply --mark-applied URL    # manually mark a job as applied
applypilot apply --mark-failed URL     # manually mark a job as failed
applypilot apply --reset-failed        # reset all failed jobs for retry
applypilot apply --gen --url URL       # generate prompt file for manual debugging
```

#### Free local engine (Gemini + Playwright, you click Submit)

Set `EXECUTION_ENGINE=local_playwright` in `~/.applypilot/.env` to skip Claude Code entirely:

```bash
EXECUTION_ENGINE=local_playwright   # default: claude_code
GEMINI_API_KEY=your-free-key
LLM_MODEL=gemini-2.5-flash          # optional; non-Gemini values fall back to gemini-2.5-flash

pip install playwright && python -m playwright install chromium
applypilot apply --url URL
```

A visible Chromium window opens with a persistent profile (`~/.applypilot/local-playwright-profile`, so SEEK/Workday logins stick). The engine reads the form controls from the DOM, asks Gemini how to fill them, then fills text fields, drop-downs, radios/checkboxes and resume/cover-letter uploads. It auto-advances only through "Next"/"Continue" buttons. It has hints for Greenhouse, Lever, Workday and SEEK (including right-to-work questions).

- **Privacy:** your name, email, phone, street address, postcode, URLs, and any tax/ID numbers in `profile.json` are sent to Gemini only as `{{PLACEHOLDER}}` tokens. They're swapped back in locally, and your password is never sent.
- **Safety gate:** automation never clicks Submit / Apply / Send. Those buttons are outlined in red and the terminal waits for you. Review the form, edit anything, and click Submit yourself. Then press Enter and answer `y` if you submitted (marked applied) or `n` (parked as `manual`; `--reset-failed` brings it back). `r` re-scans and fills the current page (e.g. after you log in), and `x` never offers that job again.
- Runs one job at a time (`--workers`/`--headless` are ignored) and costs $0 beyond your Gemini quota.

---

## CLI Reference

```
applypilot init                         # First-time setup wizard
applypilot doctor                       # Verify setup, diagnose missing requirements
applypilot run [stages...]              # Run pipeline stages (or 'all')
applypilot run --workers 4              # Parallel discovery/enrichment
applypilot run --stream                 # Concurrent stages (streaming mode)
applypilot run --min-score 8            # Override score threshold
applypilot run --dry-run                # Preview without executing
applypilot run --validation lenient     # Relax validation (recommended for Gemini free tier)
applypilot run --validation strict      # Strictest validation (retries on any banned word)
applypilot apply                        # Launch auto-apply
applypilot apply --workers 3            # Parallel browser workers
applypilot apply --dry-run              # Fill forms without submitting
applypilot apply --continuous           # Run forever, polling for new jobs
applypilot apply --headless             # Headless browser mode
applypilot apply --url URL              # Apply to a specific job
applypilot status                       # Pipeline statistics
applypilot dashboard                    # Open HTML results dashboard
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines.

---

## License

ApplyPilot is licensed under the [GNU Affero General Public License v3.0](LICENSE).

You are free to use, modify, and distribute this software. If you deploy a modified version as a service, you must release your source code under the same license.
