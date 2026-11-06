"""Run the real scorer (scorer.score_batch, the app's own prompt and parsing) over the labelled cases."""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from applypilot import config, market
from applypilot.budget import BudgetExhausted
from applypilot.scoring import scorer
from evals.scoring import metrics
from evals.scoring.dataset import Candidate

RESULTS_DIR = Path(__file__).parent / "results"


class _PinnedMarket:
    """Score each candidate in their own country, whatever search this computer has saved.

    market.current() reads the saved search's country first, so a UK search on this machine
    would otherwise give the Australian cases the UK prompt.
    """

    def __init__(self, country: str):
        self.country = country

    def __enter__(self):
        self._saved = config.load_search_config
        config.load_search_config = lambda: {"country": self.country}
        return self

    def __exit__(self, *exc):
        config.load_search_config = self._saved


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5, cwd=Path(__file__).parent).stdout.strip()
    except Exception:
        return ""


def prompt_version(country: str = "australia") -> str:
    """A short hash of the scoring prompt, so a report says which prompt it measured."""
    text = scorer.score_prompt(market.from_country(country) or market.AU)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def run(candidates: list[Candidate], repeats: int = 3, batch_size: int | None = None, seed: int = 0,
        log=print) -> dict:
    """Score every job `repeats` times; returns the full result (meta, summaries, per-job records)."""
    batch_size = max(1, int(batch_size or config.DEFAULTS["score_batch_size"]))
    records = {(c.id, j.id): {"candidate": c.id, "job": j.id, "title": j.title, "label": j.score,
                              "dealbreaker": j.dealbreaker, "why": j.why, "scores": [None] * repeats,
                              "reasoning": [""] * repeats}
               for c in candidates for j in c.jobs}
    requests = errors = 0
    stopped = ""
    started = time.time()
    for rep in range(repeats):
        for c in candidates:
            jobs = list(c.jobs)
            random.Random(f"{seed}-{rep}-{c.id}").shuffle(jobs)  # a job's place in the batch shouldn't matter
            for i in range(0, len(jobs), batch_size):
                batch = jobs[i:i + batch_size]
                postings = [j.as_posting(c.id) for j in batch]
                requests += 1
                try:
                    with _PinnedMarket(c.country):
                        got = scorer.score_batch(c.resume, postings, c.profile, c.local_areas)
                except BudgetExhausted as e:
                    stopped = f"Stopped early: {e}"
                    log(stopped)
                    break
                except Exception as e:  # one failed request leaves its jobs unscored for this repeat
                    errors += 1
                    log(f"  request failed ({c.id}, repeat {rep + 1}): {e}")
                    continue
                for job, posting in zip(batch, postings):
                    result = got.get(posting["url"])
                    if result:
                        rec = records[(c.id, job.id)]
                        rec["scores"][rep], rec["reasoning"][rep] = result["score"], result["reasoning"]
            if stopped:
                break
        log(f"  repeat {rep + 1}/{repeats} done")
        if stopped:
            break

    rows = list(records.values())
    for row in rows:
        row["ai"] = metrics.prediction(row)
    client = None
    try:
        from applypilot.llm import get_client
        client = get_client()
    except Exception:
        pass
    return {
        "meta": {
            "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": config.get_llm_provider() or "",
            "model": getattr(client, "model", "") or "",
            "prompt": prompt_version(),
            "commit": _git_commit(),
            "repeats": repeats, "batch_size": batch_size, "seed": seed,
            "requests": requests, "failed_requests": errors, "seconds": round(time.time() - started, 1),
            "labels_reviewed": all(c.labels_reviewed for c in candidates),
            "stopped": stopped,
        },
        "summary": metrics.summarise(rows),
        "by_candidate": {c.id: metrics.summarise([r for r in rows if r["candidate"] == c.id]) for c in candidates},
        "misses": metrics.biggest_misses(rows),
        "records": rows,
    }


def report(result: dict) -> str:
    """The result as a short Markdown report."""
    m, s = result["meta"], result["summary"]
    lines = [f"# Scoring eval: {m['provider']} {m['model']}".rstrip(), "",
             f"{m['when']} · prompt `{m['prompt']}` · commit `{m['commit']}` · {m['repeats']} repeat(s), "
             f"batches of {m['batch_size']} · {m['requests']} AI requests in {m['seconds']}s", ""]
    if not m["labels_reviewed"]:
        lines += ["> **The labels haven't been reviewed yet.** These numbers measure agreement with draft labels.",
                  ""]
    if m["stopped"]:
        lines += [f"> **{m['stopped']}** The results are partial.", ""]

    def fmt(v):
        return "–" if v is None else v

    lines += ["| | jobs | MAE | within 1 | exact | rank corr. | dealbreakers caught | bias | unstable |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, x in [("**all**", s), *result["by_candidate"].items()]:
        lines.append(f"| {name} | {x['jobs']} | {fmt(x['mae'])} | {fmt(x['within_1'])} | {fmt(x['exact'])} | "
                     f"{fmt(x['spearman'])} | {fmt(x['dealbreaker_recall'])} | {fmt(x['bias'])} | {x['unstable']} |")
    lines += ["", "MAE: average distance from your score. Bias: + means the AI scores higher than you. "
              "Unstable: jobs whose repeats differ by 2 or more.", ""]
    if s["dealbreakers_missed"]:
        lines += ["**Dealbreakers let through:** " + ", ".join(s["dealbreakers_missed"]), ""]
    if s["false_dealbreakers"]:
        lines += ["**Good jobs scored 3 or lower:** " + ", ".join(s["false_dealbreakers"]), ""]
    if s["missing"]:
        lines += [f"**{s['missing']} score(s) never came back** (a failed request, or the AI left a job out).", ""]
    if result["misses"]:
        by_case = {f"{r['candidate']}/{r['job']}": r for r in result["records"]}
        lines += ["## Biggest disagreements", ""]
        for miss in result["misses"]:
            rec = by_case[miss["case"]]
            said = next((t for t in rec["reasoning"] if t), "")
            lines += [f"- **{miss['case']}** ({rec['title']}): you {miss['label']}, AI {miss['ai']} "
                      f"(repeats: {', '.join('–' if x is None else str(x) for x in rec['scores'])})",
                      f"  - Your reason: {rec['why']}", f"  - AI's reason: {said or '(none)'}"]
        lines.append("")
    return "\n".join(lines)


def save(result: dict, out_dir: Path = RESULTS_DIR, name: str = "") -> Path:
    """Write <time>_<name or model>.json and .md; returns the JSON path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = result["meta"]["when"].replace(":", "").replace("-", "")[:15]
    slug = "".join(ch if ch.isalnum() or ch in "-." else "-" for ch in (name or result["meta"]["model"] or "run"))
    path = out_dir / f"{stamp}_{slug}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.with_suffix(".md").write_text(report(result) + "\n", encoding="utf-8")
    return path


def compare_files(old_path: Path, new_path: Path) -> str:
    """What changed between two saved runs: the headline numbers, then jobs that moved by 2 or more."""
    old, new = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (old_path, new_path))
    lines = [f"old: {old['meta']['model']} prompt {old['meta']['prompt']} ({old['meta']['when']})",
             f"new: {new['meta']['model']} prompt {new['meta']['prompt']} ({new['meta']['when']})", ""]
    lines += metrics.compare(old["summary"], new["summary"])
    before = {(r["candidate"], r["job"]): r for r in old["records"]}
    moved = []
    for r in new["records"]:
        b = before.get((r["candidate"], r["job"]))
        if b and b["ai"] is not None and r["ai"] is not None and abs(r["ai"] - b["ai"]) >= 2:
            moved.append(f"  {r['candidate']}/{r['job']}: {b['ai']} -> {r['ai']} (label {r['label']})")
    if moved:
        lines += ["", "Jobs that moved by 2 or more:", *moved]
    return "\n".join(lines)
