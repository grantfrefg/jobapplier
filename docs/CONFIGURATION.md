# Configuration

All your settings and data live in `~/.applypilot/`. Set the environment variable `APPLYPILOT_DIR` to use a different folder. The web UI's **Setup** tab, or `applypilot init` in the terminal, writes these files for you, so editing them by hand is optional.

| File | Purpose |
|---|---|
| `.env` | API keys, model, apply engine |
| `searches.yaml` | What and where to search, and which sources to use |
| `profile.json` | Your details and the answers used on application forms |
| `resume.txt` | Your master resume as plain text (what the AI reads) |
| `resume.pdf` | Optional; the tailored PDFs are generated from the text |
| `applypilot.db` | The job database (SQLite), including saved screening answers |
| `tailored_resumes/`, `cover_letters/` | Generated documents |
| `site_plans/` | How smart extract reads each site (delete a file to make that site re-learn) |
| `local-playwright-profile/` | The local engine's browser profile (keeps job-site logins) |
| `logs/` | Run logs; `desktop.log` for the desktop app |

---

## `.env`

| Key | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | — | Your Google Gemini key ([get one free](https://aistudio.google.com/apikey)). Check it with **Test key** in Setup |
| `LLM_MODEL` | `gemini-3.5-flash-lite` | Model for every AI step |
| `EXECUTION_ENGINE` | `claude_code` (`local_playwright` in the desktop app) | `local_playwright`: free, fills forms and **you** click Submit. `claude_code`: paid Claude Code CLI, submits automatically |
| `OPENAI_API_KEY` | — | Use OpenAI instead of Gemini (scoring and writing only; the local apply engine needs Gemini) |
| `LLM_URL`, `LLM_API_KEY` | — | Use a local OpenAI-compatible server (llama.cpp, Ollama) instead |
| `GEMINI_DAILY_LIMIT` | `500` | Gemini requests per day for your model (Google's free-tier limit). Requests are counted per US Pacific day, when Google resets them |
| `GEMINI_APPLY_RESERVE` | `50` | Requests kept for applying: finding and preparing jobs stops when only this many are left |
| `CAPSOLVER_API_KEY` | — | CAPTCHA solving, `claude_code` engine only |
| `CHROME_PATH` | auto-detected | Browser for the apply engines (Chrome, Edge or Chromium) |

Put comments on their own lines. `KEY=   # note` makes the note the value.

---

## `searches.yaml`

Example, as generated for Sydney:

```yaml
country: "australia"             # also picks Indeed's country site

defaults:
  location: "Sydney, NSW"
  hours_old: 72                  # only jobs posted in the last N hours
  results_per_site: 50           # per board, per job title

locations:
  - location: "Sydney, NSW"
    remote: false
  - location: "Australia"        # added by "Also include remote jobs"
    remote: true

location_accept: ["Sydney", "NSW", "New South Wales"]

sites: [indeed, linkedin]        # JobSpy boards
smart_extract_sites: ["Jora"]    # sites from config/sites.yaml
workday_employers: []            # employers from config/employers.yaml ([] = off)
site_priority: [linkedin, indeed, jora]

exclude_titles: ["intern", "graduate"]

queries:
  - query: "Data Analyst"
    tier: 1
  - query: "Business Analyst"
    tier: 2
```

| Key | Meaning |
|---|---|
| `country` | Country for Indeed (`australia`, `usa`, `canada`, `uk`, `new zealand`, …). Default `usa` |
| `defaults.country_indeed` | Overrides `country` for Indeed only |
| `locations` | Each search runs once per location. `remote: true` limits that search to remote jobs |
| `location_accept` | Keep jobs whose location contains one of these words; remote jobs are always kept. Empty = keep everything not rejected |
| `location_reject_non_remote` | Drop non-remote jobs whose location contains one of these |
| `sites` (or `boards`) | JobSpy boards: `indeed`, `linkedin`, `zip_recruiter`, `google`, … Default `indeed, linkedin, zip_recruiter` |
| `smart_extract_sites` | Names from `config/sites.yaml` to scrape with smart extract. **Omit = all sites; `[]` = none** |
| `workday_employers` | Keys or names from `config/employers.yaml`. **Omit = all employers; `[]` = none** |
| `site_priority` | Which listing to keep when one role is on several sites. Default `linkedin, indeed, jora` |
| `exclude_titles` | Skip jobs whose title contains any of these, matched as whole words, with no AI request |
| `score_batch_size` | Jobs scored per AI request. Default 10 |
| `queries` | Job titles to search. `tier` 1–3 is the priority; Workday uses tiers ≤ `workday_max_tier` (default 2) |
| `proxy` | `host:port:user:pass` for scraping |

---

## `profile.json`

Sections, each a set of fields shown as a form on the Setup page:

| Section | Used for |
|---|---|
| `personal` | Name, email, phone, address, LinkedIn/GitHub/portfolio URLs, job-site password. **Required:** `full_name`, `email`, `phone` |
| `work_authorization` | Right to work, sponsorship, visa/permit type. Add fields such as `tax_file_number` if forms ask; ID-like values are never sent to Gemini |
| `availability` | Start date, full-time/contract |
| `compensation` | Salary expectation, currency and range |
| `experience` | Years, education, current title/company, target role |
| `skills_boundary` | Skills the tailor may mention. It won't add skills you don't list |
| `resume_facts` | Employers, projects, school and real metrics the tailor must keep exactly |
| `eeo_voluntary` | Answers to voluntary diversity questions |

What Gemini sees: everything except the password. Identifiers (name, email, phone, street address, postcode, URLs, ID numbers) are replaced by placeholders and filled in locally. See [HOW_IT_WORKS.md](HOW_IT_WORKS.md#privacy).

---

## Bundled registries (`src/applypilot/config/`)

| File | Contents |
|---|---|
| `sites.yaml` | Smart-extract sites, including Jora, plus the original Canadian/US boards; blocked sites; base URLs; ATSs that need manual applying |
| `employers.yaml` | Workday employers (the original set is Canadian/US) |
| `searches.example.yaml` | The original example search config |
