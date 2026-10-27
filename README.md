# jobapplier

**Find Australian jobs, tailor your resume for each one, and have the application form filled in for you, then check it and click Submit yourself.** Free to run, on your own computer.

jobapplier is a fork of [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) by Pickle-Pixel, reworked for Australian job hunting and for running on Google Gemini's free tier. The app and its commands are still called **ApplyPilot**.

What's different from the original:

- **Free.** Every AI step runs on your own Google Gemini key (`gemini-3.5-flash-lite`, free tier). Form-filling uses a local browser; there's no paid Claude Code subscription, though that engine is still available.
- **You submit.** The app fills in the form and stops. Submit/Apply buttons are outlined in red, and you review and click them yourself.
- **Private.** Your name, contact details, address and ID numbers are sent to Gemini only as placeholders and filled in locally.
- **Australian job sources.** Indeed Australia, LinkedIn and Jora, filtered to your city and state. Jora ads open the employer's own application form.
- **One application per role.** The same job on several boards is detected, and you apply once.
- **Written for engineers in Australia.** Scoring knows your deal-breakers (work rights, security clearance, location, seniority). Resumes and cover letters use Australian English, never add tools you don't have, and come out as ATS-friendly A4 PDFs with your location and work rights at the top.
- **Fewer AI requests.** Contact details, uploads and questions you've answered before are filled in without the AI; Greenhouse, Lever and SEEK forms are handled specially; a daily budget keeps you inside the free tier.
- **No terminal needed.** A web UI covers setup, finding jobs and applying, and there's a double-click desktop app for Windows and Mac.

---

## Getting started

Pick the option that suits you.

### 1. Desktop app (easiest, no installs)
Download the zip for your computer, unzip it, and double-click **ApplyPilot**. The page opens in your browser.

- **Windows:** `ApplyPilot-Windows.zip`
- **Mac:** `ApplyPilot-Mac-AppleSilicon.zip` or `ApplyPilot-Mac-Intel.zip`

The first time, your computer warns that the app isn't verified. The `READ_ME_FIRST` file in the zip shows the one-time click-through. To build the zips, see [docs/DESKTOP_APP.md](docs/DESKTOP_APP.md).

### 2. Dev container (for working on the code, recommended on Windows)
1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and [VS Code](https://code.visualstudio.com/) with the **Dev Containers** extension.
2. Open this repo and choose **Reopen in Container**. The first build takes 5–10 minutes.
3. Open **http://localhost:8000**. The web UI starts automatically.
4. Watch the automated browser on the page itself, or at **http://localhost:6080** (password `vscode`).

If the page doesn't load, run `python -m applypilot ui` in VS Code's terminal (it runs inside the container).

### 3. Install with pip
```bash
pip install -e ".[ui]" -r packaging/requirements-desktop.txt
python -m playwright install chromium
applypilot ui        # opens the web UI at http://localhost:8000
```

---

## Using it

The web UI has three tabs.

**1. Setup**
- Paste your free [Gemini API key](https://aistudio.google.com/apikey) and press **Test key**.
- Paste your resume as plain text, and optionally upload the PDF.
- Choose your country, city and job titles, plus any title words to skip (e.g. *intern, graduate*).
- Fill in your profile: contact details, work rights, security clearance, notice period, relocation, salary and the other answers used on application forms.
- **Saved answers** lists the screening answers the app has remembered (yours win over the AI's); delete any you don't want reused.
- **Daily request limit** (under the Gemini key) sets the free-tier limit and how many requests to keep for applying. The header shows how many are left today.

**2. Find jobs**
- Press **Run**. It searches the job boards, scores each job against your resume, and writes a tailored resume and cover letter for the good matches, with a live log.

**3. Apply**
- Press **Apply** next to a job. A browser opens, and the app fills the form, uploads your documents and moves through "Next" pages. On a job-board ad (Jora, SEEK, Indeed) it follows the ad's Apply link to the real form first.
- Fields it already knows (name, contact details, location, uploads, answers you've given before) are filled without the AI; Gemini is only asked about the rest. Any answer you type or change before submitting is remembered for next time.
- It stops at **"Your turn: review and submit"**. Check the form, fix anything, click Submit in the browser, then tell the app what you did:
  - **I submitted it**
  - **Save for later**
  - **Fill this page again** (e.g. after you log in to SEEK)
  - **Never apply to this job**
- **Redo** on a job (or **Redo all documents**) rewrites its resume and cover letter, e.g. after you update your resume or profile. The old documents stay until the new ones pass the checks.

Everything is stored on your computer in `~/.applypilot/`.

---

## How it works

| Stage | What happens | AI requests |
|---|---|---|
| **Discover** | Searches Indeed Australia and LinkedIn (via JobSpy) and Jora. Keeps jobs in your area (plus remote, if you ask). How to read Jora's pages is learned once and saved | Jora only: ~2 the first time, then 0 |
| **De-duplicate** | Links repeat listings of the same role (same company and title, same employer link, or near-identical text). Keeps one, preferring LinkedIn, then Indeed, then Jora | None |
| **Enrich** | Fetches each job's full description and application link | Only for unusual pages |
| **Score** | Skips titles on your skip list for free, then rates the rest 1–10 against your resume, 10 jobs per request, like an engineering recruiter. Jobs you can't take (citizenship or a clearance you don't hold, onsite elsewhere, far too senior or junior) score 3 or lower | ~1 per 10 jobs |
| **Tailor + cover letter** | For each good match (score ≥ 7), rewrites your resume and writes a short, specific cover letter in one request, in Australian English. Your real employers, projects and metrics are preserved, it never adds a tool you don't have, and invented numbers or tools trigger an automatic fact-check | Usually 1 per job |
| **PDF** | A4, one-column, ATS-friendly resume (location and work rights in the header, title, employer and dates on one line) and a plain business-letter cover letter | None |
| **Apply** | Fills the form in a visible browser and waits for you to submit. Known fields and saved answers need no AI; Greenhouse, Lever and SEEK forms get site-specific handling | 0–1 per form page |

Free-tier limits are per **request per day**: `gemini-3.5-flash-lite` allows about 500. A typical day of about 200 jobs found, about 20 good matches and about 10 applications fits within that. Every request is counted (by US Pacific day, when Google resets the quota): finding and preparing jobs stops cleanly when only the apply reserve (50 by default) is left, and picks up where it left off the next day. [docs/IMPROVEMENTS.md](docs/IMPROVEMENTS.md) lists what's been done to cut usage.

More detail on each stage, privacy and the safety gate: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

---

## Configuration

Everything can be set in the web UI, and it's all saved as files in `~/.applypilot/`:

| File | What's in it |
|---|---|
| `.env` | Gemini key and model, apply engine, daily request limit and apply reserve |
| `searches.yaml` | Country, location, job titles, title skip list, which job sources to use |
| `profile.json` | Your details and form answers, including work rights, security clearance, notice period, relocation and driver's licence |
| `resume.txt` / `resume.pdf` | Your master resume |
| `applypilot.db` | Jobs, saved screening answers and today's Gemini request count |
| `site_plans/` | How each smart-extract site (Jora) is read; delete a file to make it re-learn |

Every setting is listed in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

---

## Command line

The web UI covers everything, but every step is also a command:

```
applypilot ui                      # web UI (http://localhost:8000)
applypilot init                    # setup wizard in the terminal
applypilot doctor                  # check what's installed and configured
applypilot run [stages...]         # discover enrich score tailor cover pdf (default: all)
applypilot run --min-score 8       # only tailor jobs scoring 8+
applypilot run --validation lenient    # fewer AI requests (skips the AI fact-check)
applypilot apply --url URL         # fill one application
applypilot apply --limit 5         # the next 5 best-scoring jobs
applypilot apply --reset-failed    # bring back skipped / saved-for-later jobs
applypilot redo [--url URL]        # rewrite resumes and cover letters (all, or one job)
applypilot status                  # counts per stage
applypilot dashboard               # HTML report of all jobs
```

With the free engine, `applypilot apply` stops before each Submit and waits in the terminal:
- **Enter:** done; you're then asked whether you submitted.
- **r:** fill the page again.
- **x:** never apply to this job.

---

## Development

- **Branches and PRs:** work on a branch, then open a pull request into `master`. CI runs the full test suite on Python 3.11–3.13 for every PR.
- **Tests:** `xvfb-run -a pytest tests` (the virtual display lets the browser tests run).
- **Desktop app builds:** the **Desktop app** GitHub Actions workflow. See [docs/DESKTOP_APP.md](docs/DESKTOP_APP.md).

Details are in [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Credits and licence

Built on [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) by [Pickle-Pixel](https://github.com/Pickle-Pixel). The original project isn't affiliated with applypilot.app, useapplypilot.com or other products using the name, and neither is this fork.

Licensed under the [GNU Affero General Public License v3.0](LICENSE), like the original. If you give someone this software, or run a modified version as a service for others, you must make its source code available to them under the same licence.

Job sites have their own terms of use. This tool fills in forms for you to review and submit yourself; use it considerately.
