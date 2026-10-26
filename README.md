# jobapplier

**Find Australian jobs, tailor your resume for each one, and have the application form filled in for you, then check it and click Submit yourself.** Free to run, on your own computer.

jobapplier is a fork of [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) by Pickle-Pixel, reworked for Australian job hunting and for running on Google Gemini's free tier. The app and its commands are still called **ApplyPilot**.

What's different from the original:

- **Free.** Every AI step runs on your own Google Gemini key (`gemini-3.5-flash-lite`, free tier). Form-filling uses a local browser; there's no paid Claude Code subscription, though that engine is still available.
- **You submit.** The app fills in the form and stops. Submit/Apply buttons are outlined in red, and you review and click them yourself.
- **Private.** Your name, contact details, address and ID numbers are sent to Gemini only as placeholders and filled in locally.
- **Australian job sources.** Indeed Australia, LinkedIn and Jora, filtered to your city and state.
- **One application per role.** The same job on several boards is detected, and you apply once.
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
- Fill in your profile: contact details, work rights, salary and the other answers used on application forms.

**2. Find jobs**
- Press **Run**. It searches the job boards, scores each job against your resume, and writes a tailored resume and cover letter for the good matches, with a live log.

**3. Apply**
- Press **Apply** next to a job. A browser opens, and the app fills the form, uploads your documents and moves through "Next" pages.
- It stops at **"Your turn: review and submit"**. Check the form, fix anything, click Submit in the browser, then tell the app what you did:
  - **I submitted it**
  - **Save for later**
  - **Fill this page again** (e.g. after you log in to SEEK)
  - **Never apply to this job**

Everything is stored on your computer in `~/.applypilot/`.

---

## How it works

| Stage | What happens | AI requests |
|---|---|---|
| **Discover** | Searches Indeed Australia and LinkedIn (via JobSpy) and Jora. Keeps jobs in your area (plus remote, if you ask) | Jora only: ~2 per job title |
| **De-duplicate** | Links repeat listings of the same role (same company and title, same employer link, or near-identical text). Keeps one, preferring LinkedIn, then Indeed, then Jora | None |
| **Enrich** | Fetches each job's full description and application link | Only for unusual pages |
| **Score** | Skips titles on your skip list for free, then rates the rest 1–10 against your resume, 10 jobs per request | ~1 per 10 jobs |
| **Tailor** | Rewrites your resume for each good match (score ≥ 7). Your real employers, projects and metrics are preserved, and an automatic fact-check rejects invented claims | A few per job |
| **Cover letter** | Writes a short, specific letter per job | 1+ per job |
| **PDF** | Renders the tailored resume and cover letter as PDFs | None |
| **Apply** | Fills the form in a visible browser and waits for you to submit | ~1 per form page |

Free-tier limits are per **request per day**: `gemini-3.5-flash-lite` allows about 500. A typical day of about 200 jobs found, about 20 good matches and about 10 applications fits within that. [docs/IMPROVEMENTS.md](docs/IMPROVEMENTS.md) is the plan to cut usage further.

More detail on each stage, privacy and the safety gate: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

---

## Configuration

Everything can be set in the web UI, and it's all saved as files in `~/.applypilot/`:

| File | What's in it |
|---|---|
| `.env` | Gemini key and model, apply engine |
| `searches.yaml` | Country, location, job titles, title skip list, which job sources to use |
| `profile.json` | Your details and form answers |
| `resume.txt` / `resume.pdf` | Your master resume |

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
