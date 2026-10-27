"""ApplyPilot CLI — the main entry point."""

from __future__ import annotations

import logging
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from applypilot import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)

app = typer.Typer(
    name="applypilot",
    help="AI-powered end-to-end job application pipeline.",
    no_args_is_help=True,
)
console = Console()
log = logging.getLogger(__name__)

# Valid pipeline stages (in execution order)
VALID_STAGES = ("discover", "enrich", "score", "tailor", "cover", "pdf")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bootstrap() -> None:
    """Common setup: load env, create dirs, init DB."""
    from applypilot.config import load_env, ensure_dirs
    from applypilot.database import init_db

    load_env()
    ensure_dirs()
    init_db()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold]applypilot[/bold] {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """ApplyPilot — AI-powered end-to-end job application pipeline."""


@app.command()
def init() -> None:
    """Run the first-time setup wizard (profile, resume, search config)."""
    from applypilot.wizard.init import run_wizard

    run_wizard()


@app.command()
def run(
    stages: Optional[list[str]] = typer.Argument(
        None,
        help=(
            "Pipeline stages to run. "
            f"Valid: {', '.join(VALID_STAGES)}, all. "
            "Defaults to 'all' if omitted."
        ),
    ),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for tailor/cover stages."),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel threads for discovery/enrichment stages."),
    stream: bool = typer.Option(False, "--stream", help="Run stages concurrently (streaming mode)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview stages without executing."),
    validation: str = typer.Option(
        "normal",
        "--validation",
        help=(
            "Validation strictness for tailor/cover stages. "
            "strict: banned words = errors, judge must pass. "
            "normal: banned words = warnings only (default, recommended for Gemini free tier). "
            "lenient: banned words ignored, LLM judge skipped (fastest, fewest API calls)."
        ),
    ),
) -> None:
    """Run pipeline stages: discover, enrich, score, tailor, cover, pdf."""
    _bootstrap()

    from applypilot.pipeline import run_pipeline

    stage_list = stages if stages else ["all"]

    # Validate stage names
    for s in stage_list:
        if s != "all" and s not in VALID_STAGES:
            console.print(
                f"[red]Unknown stage:[/red] '{s}'. "
                f"Valid stages: {', '.join(VALID_STAGES)}, all"
            )
            raise typer.Exit(code=1)

    # Gate AI stages behind Tier 2
    llm_stages = {"score", "tailor", "cover"}
    if any(s in stage_list for s in llm_stages) or "all" in stage_list:
        from applypilot.config import check_tier
        check_tier(2, "AI scoring/tailoring")

    # Validate the --validation flag value
    valid_modes = ("strict", "normal", "lenient")
    if validation not in valid_modes:
        console.print(
            f"[red]Invalid --validation value:[/red] '{validation}'. "
            f"Choose from: {', '.join(valid_modes)}"
        )
        raise typer.Exit(code=1)

    result = run_pipeline(
        stages=stage_list,
        min_score=min_score,
        dry_run=dry_run,
        stream=stream,
        workers=workers,
        validation_mode=validation,
    )

    if result.get("errors"):
        raise typer.Exit(code=1)


@app.command()
def apply(
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Max applications to submit."),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of parallel browser workers."),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for job selection."),
    model: str = typer.Option("haiku", "--model", "-m", help="Claude model name."),
    continuous: bool = typer.Option(False, "--continuous", "-c", help="Run forever, polling for new jobs."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview actions without submitting."),
    headless: bool = typer.Option(False, "--headless", help="Run browsers in headless mode."),
    url: Optional[str] = typer.Option(None, "--url", help="Apply to a specific job URL."),
    gen: bool = typer.Option(False, "--gen", help="Generate prompt file for manual debugging instead of running."),
    mark_applied: Optional[str] = typer.Option(None, "--mark-applied", help="Manually mark a job URL as applied."),
    mark_failed: Optional[str] = typer.Option(None, "--mark-failed", help="Manually mark a job URL as failed (provide URL)."),
    fail_reason: Optional[str] = typer.Option(None, "--fail-reason", help="Reason for --mark-failed."),
    reset_failed: bool = typer.Option(False, "--reset-failed", help="Reset all failed jobs for retry."),
) -> None:
    """Launch auto-apply to submit job applications."""
    _bootstrap()

    from applypilot.config import check_tier, PROFILE_PATH as _profile_path
    from applypilot.database import get_connection

    # --- Utility modes (no Chrome/Claude needed) ---

    if mark_applied:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_applied, "applied")
        console.print(f"[green]Marked as applied:[/green] {mark_applied}")
        return

    if mark_failed:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_failed, "failed", reason=fail_reason)
        console.print(f"[yellow]Marked as failed:[/yellow] {mark_failed} ({fail_reason or 'manual'})")
        return

    if reset_failed:
        from applypilot.apply.launcher import reset_failed as do_reset
        count = do_reset()
        console.print(f"[green]Reset {count} failed job(s) for retry.[/green]")
        return

    # --- Full apply mode ---

    # Check 1: Tier 3 required (Claude Code CLI + Chrome, or Gemini + Playwright locally)
    check_tier(3, "auto-apply")

    # Check 2: Profile exists
    if not _profile_path.exists():
        console.print(
            "[red]Profile not found.[/red]\n"
            "Run [bold]applypilot init[/bold] to create your profile first."
        )
        raise typer.Exit(code=1)

    # Check 3: Tailored resumes exist (skip for --gen with --url)
    if not (gen and url):
        conn = get_connection()
        ready = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE (tailored_resume_path IS NOT NULL OR own_resume_path IS NOT NULL) "
            "AND applied_at IS NULL"
        ).fetchone()[0]
        if ready == 0:
            console.print(
                "[red]No tailored resumes ready.[/red]\n"
                "Run [bold]applypilot run score tailor[/bold] first to prepare applications."
            )
            raise typer.Exit(code=1)

    if gen:
        from applypilot.apply.launcher import gen_prompt, BASE_CDP_PORT
        target = url or ""
        if not target:
            console.print("[red]--gen requires --url to specify which job.[/red]")
            raise typer.Exit(code=1)
        prompt_file = gen_prompt(target, min_score=min_score, model=model)
        if not prompt_file:
            console.print("[red]No matching job found for that URL.[/red]")
            raise typer.Exit(code=1)
        mcp_path = _profile_path.parent / ".mcp-apply-0.json"
        console.print(f"[green]Wrote prompt to:[/green] {prompt_file}")
        console.print(f"\n[bold]Run manually:[/bold]")
        console.print(
            f"  claude --model {model} -p "
            f"--mcp-config {mcp_path} "
            f"--permission-mode bypassPermissions < {prompt_file}"
        )
        return

    from applypilot.apply.launcher import main as apply_main

    effective_limit = limit if limit is not None else (0 if continuous else 1)

    from applypilot.config import get_execution_engine, get_local_engine_model, ENGINE_LOCAL_PLAYWRIGHT
    engine = get_execution_engine()

    console.print("\n[bold blue]Launching Auto-Apply[/bold blue]")
    console.print(f"  Engine:   {engine}")
    if engine == ENGINE_LOCAL_PLAYWRIGHT:
        console.print(f"  Gemini:   {get_local_engine_model()}")
        console.print("  [dim]Forms are filled locally; YOU click Submit after reviewing.[/dim]")
    console.print(f"  Limit:    {'unlimited' if continuous else effective_limit}")
    console.print(f"  Workers:  {workers}")
    if engine != ENGINE_LOCAL_PLAYWRIGHT:
        console.print(f"  Model:    {model}")
    console.print(f"  Headless: {headless}")
    console.print(f"  Dry run:  {dry_run}")
    if url:
        console.print(f"  Target:   {url}")
    console.print()

    apply_main(
        limit=effective_limit,
        target_url=url,
        min_score=min_score,
        headless=headless,
        model=model,
        dry_run=dry_run,
        continuous=continuous,
        workers=workers,
    )


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to listen on (keep it local)."),
    port: int = typer.Option(8000, "--port", "-p", help="Port for the web UI."),
) -> None:
    """Open the web UI: setup, find jobs, and apply with on-screen approval."""
    try:
        from applypilot.ui.server import serve
    except ImportError:
        console.print(
            "[red]The web UI needs extra packages.[/red]\n"
            "Install them with: [bold]pip install \"applypilot[ui]\"[/bold] "
            "(or [bold]pip install -e \".[ui]\"[/bold] from a checkout)"
        )
        raise typer.Exit(code=1)

    shown_host = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    console.print(f"[bold blue]ApplyPilot UI[/bold blue] → http://{shown_host}:{port}")
    console.print("[dim]Ctrl+C to stop[/dim]")
    serve(host=host, port=port)


@app.command()
def redo(
    url: Optional[str] = typer.Option(None, "--url", help="Only this job (and its listings on other sites)."),
    validation: str = typer.Option("normal", "--validation", help="strict, normal or lenient."),
    pending: bool = typer.Option(False, "--pending", help="Only write the jobs already marked for Redo."),
) -> None:
    """Rewrite tailored resumes and cover letters for jobs not yet applied to.

    Old documents stay until the new ones pass the checks. Jobs stuck "in
    progress" from an unfinished apply run are put back in the apply queue,
    so don't run this while applying. Also re-picks the listing to use when a
    role is on several sites (site_priority: LinkedIn > Indeed > Jora).
    """
    _bootstrap()

    from applypilot.config import check_tier
    from applypilot.database import get_connection, redo_documents
    from applypilot.scoring.pdf import batch_convert
    from applypilot.scoring.tailor import run_tailoring

    check_tier(2, "AI tailoring")
    if not pending:
        marked = redo_documents(get_connection(), url)
        console.print(f"Rewriting documents for {marked} job(s)...")
    result = run_tailoring(limit=0, validation_mode=validation, redo_only=True)
    batch_convert()
    console.print(f"Done: {result['approved']} rewritten. Jobs whose new version didn't pass keep their old documents.")


@app.command()
def ats(
    url: Optional[str] = typer.Option(None, "--url", help="Only this job."),
) -> None:
    """ATS check of each tailored resume (Australian employers' systems): score, issues, missing keywords.

    No AI requests. A guide to how a parser and a keyword search see the resume.
    """
    _bootstrap()
    from pathlib import Path

    from applypilot.config import RESUME_PATH
    from applypilot.database import get_connection
    from applypilot.scoring import ats as ats_mod

    original = RESUME_PATH.read_text(encoding="utf-8") if RESUME_PATH.exists() else ""
    rows = get_connection().execute(
        "SELECT * FROM jobs WHERE tailored_resume_path IS NOT NULL AND duplicate_of IS NULL "
        "AND applied_at IS NULL" + (" AND url = ?" if url else "") + " ORDER BY fit_score DESC",
        (url,) if url else ()).fetchall()
    for row in rows:
        job = dict(row)
        try:
            path = Path(job["tailored_resume_path"])
            pdf = path.with_suffix(".pdf")
            report = ats_mod.check(path.read_text(encoding="utf-8"), job, original,
                                   pdf_path=pdf if pdf.exists() else None)
        except OSError:
            continue
        console.print(f"\n[bold]{report['score']:>3}/100[/bold]  {job['title']} ({job.get('company') or job['site']})"
                      f"  keywords {report['keyword_match']}%")
        for issue in report["issues"]:
            console.print(f"       - {issue}")
    if not rows:
        console.print("No tailored resumes yet.")


@app.command()
def login(
    url: Optional[str] = typer.Option(None, "--url", help="Page to open (default: Google, LinkedIn and SEEK)."),
) -> None:
    """Open the free engine's browser as a normal window to log in to Google, LinkedIn, SEEK...

    Google blocks sign-in while a browser is controlled by automation. Log in
    in this window and close it: the engine's browser reuses the logins.
    """
    _bootstrap()
    from applypilot.apply.login_browser import open_login_browser

    proc = open_login_browser([url] if url else None)
    console.print("Log in in the browser window, then close it to save the logins...")
    proc.wait()
    console.print("[green]Logins saved.[/green] The apply engine will use them.")


@app.command()
def locate(
    min_score: int = typer.Option(7, "--min-score", help="Only jobs scoring at least this."),
    recheck: bool = typer.Option(False, "--recheck", help="Check jobs that were already checked again."),
    limit: int = typer.Option(0, "--limit", help="Check at most this many jobs (0 = all)."),
) -> None:
    """Find out where each ready job applies (Greenhouse, Workday, SEEK...) and whether it needs an account.

    Opens the free engine's visible browser, follows each ad's Apply link and
    records where it lands. Nothing is filled in and no AI requests are used.
    """
    _bootstrap()
    import logging

    from applypilot.apply.sites import locate_jobs

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    locate_jobs(min_score=min_score, recheck=recheck, limit=limit)


@app.command()
def status() -> None:
    """Show pipeline statistics from the database."""
    _bootstrap()

    from applypilot.database import get_stats

    stats = get_stats()

    console.print("\n[bold]ApplyPilot Pipeline Status[/bold]\n")

    # Summary table
    summary = Table(title="Pipeline Overview", show_header=True, header_style="bold cyan")
    summary.add_column("Metric", style="bold")
    summary.add_column("Count", justify="right")

    summary.add_row("Total jobs discovered", str(stats["total"]))
    summary.add_row("With full description", str(stats["with_description"]))
    summary.add_row("Pending enrichment", str(stats["pending_detail"]))
    summary.add_row("Enrichment errors", str(stats["detail_errors"]))
    summary.add_row("Scored by LLM", str(stats["scored"]))
    summary.add_row("Pending scoring", str(stats["unscored"]))
    summary.add_row("Tailored resumes", str(stats["tailored"]))
    summary.add_row("Pending tailoring (7+)", str(stats["untailored_eligible"]))
    summary.add_row("Cover letters", str(stats["with_cover_letter"]))
    summary.add_row("Ready to apply", str(stats["ready_to_apply"]))
    summary.add_row("Applied", str(stats["applied"]))
    summary.add_row("Apply errors", str(stats["apply_errors"]))

    console.print(summary)

    # Score distribution
    if stats["score_distribution"]:
        dist_table = Table(title="\nScore Distribution", show_header=True, header_style="bold yellow")
        dist_table.add_column("Score", justify="center")
        dist_table.add_column("Count", justify="right")
        dist_table.add_column("Bar")

        max_count = max(count for _, count in stats["score_distribution"]) or 1
        for score, count in stats["score_distribution"]:
            bar_len = int(count / max_count * 30)
            if score >= 7:
                color = "green"
            elif score >= 5:
                color = "yellow"
            else:
                color = "red"
            bar = f"[{color}]{'=' * bar_len}[/{color}]"
            dist_table.add_row(str(score), str(count), bar)

        console.print(dist_table)

    # By site
    if stats["by_site"]:
        site_table = Table(title="\nJobs by Source", show_header=True, header_style="bold magenta")
        site_table.add_column("Site")
        site_table.add_column("Count", justify="right")

        for site, count in stats["by_site"]:
            site_table.add_row(site or "Unknown", str(count))

        console.print(site_table)

    console.print()


@app.command()
def dashboard() -> None:
    """Generate and open the HTML dashboard in your browser."""
    _bootstrap()

    from applypilot.view import open_dashboard

    open_dashboard()


@app.command()
def doctor() -> None:
    """Check your setup and diagnose missing requirements."""
    import shutil
    from applypilot.config import (
        load_env, PROFILE_PATH, RESUME_PATH, RESUME_PDF_PATH,
        SEARCH_CONFIG_PATH, ENV_PATH, get_chrome_path,
    )

    load_env()

    ok_mark = "[green]OK[/green]"
    fail_mark = "[red]MISSING[/red]"
    warn_mark = "[yellow]WARN[/yellow]"

    results: list[tuple[str, str, str]] = []  # (check, status, note)

    # --- Tier 1 checks ---
    # Profile
    if PROFILE_PATH.exists():
        results.append(("profile.json", ok_mark, str(PROFILE_PATH)))
    else:
        results.append(("profile.json", fail_mark, "Run 'applypilot init' to create"))

    # Resume
    if RESUME_PATH.exists():
        results.append(("resume.txt", ok_mark, str(RESUME_PATH)))
    elif RESUME_PDF_PATH.exists():
        results.append(("resume.txt", warn_mark, "Only PDF found — plain-text needed for AI stages"))
    else:
        results.append(("resume.txt", fail_mark, "Run 'applypilot init' to add your resume"))

    # Search config
    if SEARCH_CONFIG_PATH.exists():
        results.append(("searches.yaml", ok_mark, str(SEARCH_CONFIG_PATH)))
    else:
        results.append(("searches.yaml", warn_mark, "Will use example config — run 'applypilot init'"))

    # jobspy (discovery dep installed separately)
    try:
        import jobspy  # noqa: F401
        results.append(("python-jobspy", ok_mark, "Job board scraping available"))
    except ImportError:
        results.append(("python-jobspy", warn_mark,
                        "pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex"))

    # --- Tier 2 checks ---
    import os
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    has_local = bool(os.environ.get("LLM_URL"))
    if has_gemini:
        from applypilot.config import DEFAULT_GEMINI_MODEL
        model = os.environ.get("LLM_MODEL") or DEFAULT_GEMINI_MODEL
        results.append(("LLM API key", ok_mark, f"Gemini ({model})"))
    elif has_openai:
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        results.append(("LLM API key", ok_mark, f"OpenAI ({model})"))
    elif has_local:
        results.append(("LLM API key", ok_mark, f"Local: {os.environ.get('LLM_URL')}"))
    else:
        results.append(("LLM API key", fail_mark,
                        "Set GEMINI_API_KEY in ~/.applypilot/.env (run 'applypilot init')"))

    # --- Tier 3 checks ---
    from applypilot.config import (
        get_execution_engine, get_local_engine_model, has_playwright, ENGINE_LOCAL_PLAYWRIGHT,
    )
    engine = get_execution_engine()
    results.append(("Apply engine", ok_mark, f"EXECUTION_ENGINE={engine}"))

    if engine == ENGINE_LOCAL_PLAYWRIGHT:
        if os.environ.get("GEMINI_API_KEY"):
            results.append(("Gemini (local engine)", ok_mark, get_local_engine_model()))
        else:
            results.append(("Gemini (local engine)", fail_mark,
                            "Set GEMINI_API_KEY (needed for local_playwright)"))
        if has_playwright():
            results.append(("Playwright", ok_mark, "Run 'playwright install chromium' if the browser won't start"))
        else:
            results.append(("Playwright", fail_mark,
                            "pip install playwright && playwright install chromium"))
    else:
        # Claude Code CLI
        claude_bin = shutil.which("claude")
        if claude_bin:
            results.append(("Claude Code CLI", ok_mark, claude_bin))
        else:
            results.append(("Claude Code CLI", fail_mark,
                            "Install from https://claude.ai/code (needed for auto-apply)"))

        # Chrome
        try:
            chrome_path = get_chrome_path()
            results.append(("Chrome/Chromium", ok_mark, chrome_path))
        except FileNotFoundError:
            results.append(("Chrome/Chromium", fail_mark,
                            "Install Chrome or set CHROME_PATH env var (needed for auto-apply)"))

        # Node.js / npx (for Playwright MCP)
        npx_bin = shutil.which("npx")
        if npx_bin:
            results.append(("Node.js (npx)", ok_mark, npx_bin))
        else:
            results.append(("Node.js (npx)", fail_mark,
                            "Install Node.js 18+ from nodejs.org (needed for auto-apply)"))

    # CapSolver (optional)
    capsolver = os.environ.get("CAPSOLVER_API_KEY")
    if capsolver:
        results.append(("CapSolver API key", ok_mark, "CAPTCHA solving enabled"))
    else:
        results.append(("CapSolver API key", "[dim]optional[/dim]",
                        "Set CAPSOLVER_API_KEY in .env for CAPTCHA solving"))

    # --- Render results ---
    console.print()
    console.print("[bold]ApplyPilot Doctor[/bold]\n")

    col_w = max(len(r[0]) for r in results) + 2
    for check, status, note in results:
        pad = " " * (col_w - len(check))
        console.print(f"  {check}{pad}{status}  [dim]{note}[/dim]")

    console.print()

    # Tier summary
    from applypilot.config import get_tier, TIER_LABELS
    tier = get_tier()
    console.print(f"[bold]Current tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]")

    tier3_needs = (
        "GEMINI_API_KEY + Playwright" if engine == ENGINE_LOCAL_PLAYWRIGHT
        else "Claude Code CLI + Chrome + Node.js"
    )
    if tier == 1:
        console.print("[dim]  → Tier 2 unlocks: scoring, tailoring, cover letters (needs LLM API key)[/dim]")
        console.print(f"[dim]  → Tier 3 unlocks: auto-apply (needs {tier3_needs})[/dim]")
    elif tier == 2:
        console.print(f"[dim]  → Tier 3 unlocks: auto-apply (needs {tier3_needs})[/dim]")

    console.print()


if __name__ == "__main__":
    app()
