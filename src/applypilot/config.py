"""ApplyPilot configuration: paths, platform detection, user data."""

import os
import platform
import shutil
from pathlib import Path

# User data directory — all user-specific files live here
APP_DIR = Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))

# Core paths
DB_PATH = APP_DIR / "applypilot.db"
PROFILE_PATH = APP_DIR / "profile.json"
RESUME_PATH = APP_DIR / "resume.txt"
RESUME_PDF_PATH = APP_DIR / "resume.pdf"
SEARCH_CONFIG_PATH = APP_DIR / "searches.yaml"
ENV_PATH = APP_DIR / ".env"

# Generated output
TAILORED_DIR = APP_DIR / "tailored_resumes"
COVER_LETTER_DIR = APP_DIR / "cover_letters"
LOG_DIR = APP_DIR / "logs"

# Chrome worker isolation
CHROME_WORKER_DIR = APP_DIR / "chrome-workers"
APPLY_WORKER_DIR = APP_DIR / "apply-workers"

# Package-shipped config (YAML registries)
PACKAGE_DIR = Path(__file__).parent
CONFIG_DIR = PACKAGE_DIR / "config"


def get_chrome_path() -> str:
    """Auto-detect Chrome/Chromium executable path, cross-platform.

    Override with CHROME_PATH environment variable.
    """
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    system = platform.system()

    if system == "Windows":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    else:  # Linux
        candidates = []
        for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))

    for c in candidates:
        if c and c.exists():
            return str(c)

    # Fall back to PATH search
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found

    raise FileNotFoundError(
        "Chrome/Chromium not found. Install Chrome or set CHROME_PATH environment variable."
    )


def get_chrome_user_data() -> Path:
    """Default Chrome user data directory, cross-platform."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    else:
        return Path.home() / ".config" / "google-chrome"


def ensure_dirs():
    """Create all required directories."""
    for d in [APP_DIR, TAILORED_DIR, COVER_LETTER_DIR, LOG_DIR, CHROME_WORKER_DIR, APPLY_WORKER_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_profile() -> dict:
    """Load user profile from ~/.applypilot/profile.json."""
    import json
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"Profile not found at {PROFILE_PATH}. Run `applypilot init` first."
        )
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def load_search_config() -> dict:
    """Load search configuration from ~/.applypilot/searches.yaml."""
    import yaml
    if not SEARCH_CONFIG_PATH.exists():
        # Fall back to package-shipped example
        example = CONFIG_DIR / "searches.example.yaml"
        if example.exists():
            return yaml.safe_load(example.read_text(encoding="utf-8"))
        return {}
    return yaml.safe_load(SEARCH_CONFIG_PATH.read_text(encoding="utf-8"))


def load_sites_config() -> dict:
    """Load sites.yaml configuration (sites list, manual_ats, blocked, etc.)."""
    import yaml
    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def is_manual_ats(url: str | None) -> bool:
    """Check if a URL routes through an ATS that requires manual application."""
    if not url:
        return False
    sites_cfg = load_sites_config()
    domains = sites_cfg.get("manual_ats", [])
    url_lower = url.lower()
    return any(domain in url_lower for domain in domains)


def load_blocked_sites() -> tuple[set[str], list[str]]:
    """Load blocked sites and URL patterns from sites.yaml.

    Returns:
        (blocked_site_names, blocked_url_patterns)
    """
    cfg = load_sites_config()
    blocked = cfg.get("blocked", {})
    sites = set(blocked.get("sites", []))
    patterns = blocked.get("url_patterns", [])
    return sites, patterns


# ---------------------------------------------------------------------------
# Location filtering (shared by every discovery source)
# ---------------------------------------------------------------------------

_REMOTE_WORDS = ("remote", "anywhere", "work from home", "wfh", "distributed")

# Abbreviation -> full name, so "Sydney, NSW" also accepts "New South Wales"
AU_STATES = {
    "nsw": "New South Wales", "vic": "Victoria", "qld": "Queensland",
    "wa": "Western Australia", "sa": "South Australia", "tas": "Tasmania",
    "act": "Australian Capital Territory", "nt": "Northern Territory",
}


def location_accept_patterns(location: str) -> list[str]:
    """Accept patterns for a location like "Sydney, NSW" -> Sydney, NSW, New South Wales."""
    patterns: list[str] = []
    for part in (p.strip() for p in location.split(",")):
        if not part or part.lower() in _REMOTE_WORDS:
            continue
        patterns.append(part)
        full = AU_STATES.get(part.lower())
        if full:
            patterns.append(full)
    return list(dict.fromkeys(patterns))


def load_location_filter(search_cfg: dict | None = None) -> tuple[list[str], list[str]]:
    """(accept, reject) location patterns from searches.yaml."""
    if search_cfg is None:
        search_cfg = load_search_config()
    accept = list(search_cfg.get("location_accept") or [])
    reject = list(search_cfg.get("location_reject_non_remote") or [])
    return accept, reject


def location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Should a job at `location` be kept?

    Unknown and remote locations are kept. Otherwise reject patterns win,
    then the job must match an accept pattern -- unless no accept patterns
    are configured, in which case everything not rejected is kept.
    Patterns match whole words, so "WA" doesn't match "Warsaw".
    """
    import re
    if not location:
        return True
    loc = location.lower()
    if any(r in loc for r in _REMOTE_WORDS):
        return True

    def _hit(pattern: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(pattern.lower())}(?!\w)", loc) is not None

    if any(_hit(r) for r in reject):
        return False
    if not accept:
        return True
    return any(_hit(a) for a in accept)


def source_allowlist(search_cfg: dict, key: str) -> set[str] | None:
    """Names listed under `key` (e.g. smart_extract_sites), or None = use all."""
    if key not in search_cfg or search_cfg[key] is None:
        return None
    return {str(v).lower() for v in search_cfg[key]}


def load_blocked_sso() -> list[str]:
    """Load blocked SSO domains from sites.yaml."""
    cfg = load_sites_config()
    return cfg.get("blocked_sso", [])


def load_base_urls() -> dict[str, str | None]:
    """Load site base URLs for URL resolution from sites.yaml."""
    cfg = load_sites_config()
    return cfg.get("base_urls", {})


# ---------------------------------------------------------------------------
# Default values — referenced across modules instead of magic numbers
# ---------------------------------------------------------------------------

DEFAULTS = {
    "min_score": 7,
    "max_apply_attempts": 3,
    "max_tailor_attempts": 5,
    "poll_interval": 60,
    "apply_timeout": 300,
    "viewport": "1280x900",
}


def load_env():
    """Load environment variables from ~/.applypilot/.env if it exists."""
    from dotenv import load_dotenv
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    # Also try CWD .env as fallback
    load_dotenv()


# ---------------------------------------------------------------------------
# Execution engine — which driver performs Stage 6 (auto-apply)
# ---------------------------------------------------------------------------

ENGINE_CLAUDE_CODE = "claude_code"
ENGINE_LOCAL_PLAYWRIGHT = "local_playwright"
EXECUTION_ENGINES = (ENGINE_CLAUDE_CODE, ENGINE_LOCAL_PLAYWRIGHT)

# Default Gemini model for the local Playwright engine
LOCAL_ENGINE_DEFAULT_MODEL = "gemini-2.5-flash"

# Persistent browser profile for the local engine (keeps SEEK/Workday logins)
LOCAL_BROWSER_PROFILE_DIR = APP_DIR / "local-playwright-profile"


def get_execution_engine() -> str:
    """Return the configured apply engine from EXECUTION_ENGINE.

    Unset, empty, or unrecognised values fall back to "claude_code" so the
    original paid Claude Code flow stays the default.
    """
    raw = os.environ.get("EXECUTION_ENGINE", "").strip().lower()
    if raw in EXECUTION_ENGINES:
        return raw
    if raw:
        import logging
        logging.getLogger(__name__).warning(
            "Unknown EXECUTION_ENGINE=%r — falling back to %r. Valid values: %s",
            raw, ENGINE_CLAUDE_CODE, ", ".join(EXECUTION_ENGINES),
        )
    return ENGINE_CLAUDE_CODE


def get_local_engine_model() -> str:
    """Gemini model for the local engine: LLM_MODEL if it names a Gemini model."""
    model = os.environ.get("LLM_MODEL", "").strip()
    if model.lower().startswith("gemini"):
        return model
    return LOCAL_ENGINE_DEFAULT_MODEL


def has_playwright() -> bool:
    """True if the Playwright Python package is importable."""
    import importlib.util
    return importlib.util.find_spec("playwright") is not None


# Where the desktop app keeps Playwright's Chromium (outside the app bundle,
# so installing it never touches a signed .app / Program Files).
BROWSERS_DIR = APP_DIR / "browsers"


def playwright_browsers_dir() -> Path:
    """Directory Playwright installs browsers into (honours PLAYWRIGHT_BROWSERS_PATH)."""
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        return Path(env)
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
    if system == "Darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def has_playwright_chromium() -> bool:
    """True if a Playwright-managed Chromium has been downloaded."""
    root = playwright_browsers_dir()
    return root.is_dir() and any(root.glob("chromium-*"))


def find_system_browsers() -> list[str]:
    """Installed Chrome / Edge / Chromium executables the local engine can drive.

    Edge ships with every Windows PC, so Windows users normally need no download.
    """
    found: list[str] = []
    try:
        found.append(get_chrome_path())
    except FileNotFoundError:
        pass

    system = platform.system()
    if system == "Windows":
        candidates = [
            Path(os.environ.get(var, default)) / "Microsoft/Edge/Application/msedge.exe"
            for var, default in (("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                                 ("PROGRAMFILES", r"C:\Program Files"))
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    else:
        candidates = [Path(p) for p in map(shutil.which, ("microsoft-edge", "microsoft-edge-stable")) if p]

    for c in candidates:
        if c.exists() and str(c) not in found:
            found.append(str(c))
    return found


def is_desktop_app() -> bool:
    """True when running as the double-click desktop app (friend mode)."""
    return os.environ.get("APPLYPILOT_DESKTOP") == "1"


# ---------------------------------------------------------------------------
# Tier system — feature gating by installed dependencies
# ---------------------------------------------------------------------------

TIER_LABELS = {
    1: "Discovery",
    2: "AI Scoring & Tailoring",
    3: "Full Auto-Apply",
}

TIER_COMMANDS: dict[int, list[str]] = {
    1: ["init", "run discover", "run enrich", "status", "dashboard"],
    2: ["run score", "run tailor", "run cover", "run pdf", "run"],
    3: ["apply"],
}


def get_tier() -> int:
    """Detect the current tier based on available dependencies.

    Tier 1 (Discovery):            Python + pip
    Tier 2 (AI Scoring & Tailoring): + LLM API key
    Tier 3 (Full Auto-Apply):       + Claude Code CLI + Chrome
                                    (or, with EXECUTION_ENGINE=local_playwright,
                                     + GEMINI_API_KEY + Playwright)
    """
    load_env()

    has_llm = any(os.environ.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL"))
    if not has_llm:
        return 1

    if get_execution_engine() == ENGINE_LOCAL_PLAYWRIGHT:
        if os.environ.get("GEMINI_API_KEY") and has_playwright():
            return 3
        return 2

    has_claude = shutil.which("claude") is not None
    try:
        get_chrome_path()
        has_chrome = True
    except FileNotFoundError:
        has_chrome = False

    if has_claude and has_chrome:
        return 3

    return 2


def check_tier(required: int, feature: str) -> None:
    """Raise SystemExit with a clear message if the current tier is too low.

    Args:
        required: Minimum tier needed (1, 2, or 3).
        feature: Human-readable description of the feature being gated.
    """
    current = get_tier()
    if current >= required:
        return

    from rich.console import Console
    _console = Console(stderr=True)

    missing: list[str] = []
    if required >= 2 and not any(os.environ.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL")):
        missing.append("LLM API key — run [bold]applypilot init[/bold] or set GEMINI_API_KEY")
    if required >= 3 and get_execution_engine() == ENGINE_LOCAL_PLAYWRIGHT:
        if not os.environ.get("GEMINI_API_KEY"):
            missing.append("GEMINI_API_KEY — required by EXECUTION_ENGINE=local_playwright")
        if not has_playwright():
            missing.append("Playwright — run [bold]pip install playwright && playwright install chromium[/bold]")
    elif required >= 3:
        if not shutil.which("claude"):
            missing.append("Claude Code CLI — install from [bold]https://claude.ai/code[/bold]")
        try:
            get_chrome_path()
        except FileNotFoundError:
            missing.append("Chrome/Chromium — install or set CHROME_PATH")

    _console.print(
        f"\n[red]'{feature}' requires {TIER_LABELS.get(required, f'Tier {required}')} (Tier {required}).[/red]\n"
        f"Current tier: {TIER_LABELS.get(current, f'Tier {current}')} (Tier {current})."
    )
    if missing:
        _console.print("\n[yellow]Missing:[/yellow]")
        for m in missing:
            _console.print(f"  - {m}")
    _console.print()
    raise SystemExit(1)
