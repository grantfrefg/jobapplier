"""The job market you're applying in: Australia (the default) or the United Kingdom.

Everything that differs by country lives here: spelling, currency and pay conventions,
hours, what the document is called, phone format, recruiter wording. The market comes
from the search's Country (Setup -> What jobs to look for), else the profile's country.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Market:
    key: str               # "au" / "uk"
    country: str           # "Australia"
    adjective: str         # "Australian" (as in "Australian employers")
    in_country: str        # "in Australia"
    currency: str          # "AUD"
    symbol: str            # "$"
    doc: str               # "resume" / "CV"
    style: str             # house style line for the AI prompts
    hours: float           # full-time hours a week
    days: int              # working days a year, for day rates
    dial: str              # "+61"
    pay_note: str          # what a salary figure includes, for scoring and the candidate brief
    package_rule: str      # how to answer "package"/"total" salary questions on forms
    plus_example: str      # free-text salary example
    criteria: str          # selection-criteria style questions, by name
    spelling: str          # "Australian English"

    @property
    def doc_title(self) -> str:
        return self.doc[0].upper() + self.doc[1:]

    @property
    def documents(self) -> str:
        return f"{self.doc}s" if self.doc == "resume" else "CVs"


AU = Market(
    key="au", country="Australia", adjective="Australian", in_country="in Australia",
    currency="AUD", symbol="$", doc="resume",
    style=("Use Australian English spelling (optimise, organisation, analyse, modelling, "
           "licence as a noun, programme only for non-software programs). Dates as "
           "\"Mar 2021 - Jun 2023\" in resumes and DD/MM/YYYY in forms. Currency is AUD."),
    hours=38, days=230, dial="+61",
    pay_note="base, excluding super",
    package_rule=("If the question asks for a package / \"including super\" / total remuneration, add the 12% "
                  "super guarantee (base x 1.12)."),
    plus_example="$120,000 plus super",
    criteria="selection criteria",
    spelling="Australian English",
)

UK = Market(
    key="uk", country="United Kingdom", adjective="UK", in_country="in the UK",
    currency="GBP", symbol="£", doc="CV",
    style=("Use British English spelling (optimise, organisation, analyse, modelling, "
           "licence as a noun, programme only for non-software programs). Dates as "
           "\"Mar 2021 - Jun 2023\" in CVs and DD/MM/YYYY in forms. Currency is GBP."),
    hours=37.5, days=225, dial="+44",
    pay_note="base, excluding pension and bonus",
    package_rule=("UK salaries are quoted before pension: give the base figure. If the question asks for a total "
                  "package, add the employer pension only if it states a percentage."),
    plus_example="£55,000 plus pension",
    criteria="competency-based questions",
    spelling="British English",
)

MARKETS = {"au": AU, "uk": UK}
_UK_NAMES = {"uk", "united kingdom", "great britain", "britain", "gb", "england", "scotland", "wales",
             "northern ireland"}


def from_country(name: str | None) -> Market | None:
    """The market for a country name ("uk", "United Kingdom", "australia"), or None if unknown."""
    low = str(name or "").strip().lower()
    if low in _UK_NAMES:
        return UK
    if low in ("au", "australia"):
        return AU
    return None


def current(profile: dict | None = None) -> Market:
    """The market in use: the search's country, else the profile's, else Australia."""
    from applypilot import config
    try:
        found = from_country((config.load_search_config() or {}).get("country"))
        if found:
            return found
    except Exception:  # no search saved yet
        pass
    if profile is None:
        try:
            profile = config.load_profile()
        except Exception:
            profile = {}
    return from_country(((profile or {}).get("personal") or {}).get("country")) or AU


def localise(text: str, swaps: list[tuple[str, str]], mk: Market | None = None) -> str:
    """An Australian prompt for the market in use: each (Australian passage, UK passage) swapped in.
    Every passage must be there, so a prompt edit can't silently drop a UK change."""
    if (mk or current()).key == "au":
        return text
    for au, uk in swaps:
        if au not in text:
            raise ValueError(f"market.localise: passage not found: {au[:60]!r}")
        text = text.replace(au, uk)
    return text
