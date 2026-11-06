"""Metrics for the scoring eval: how close the AI's scores are to the human labels, and how steady.

Every function takes the per-job records the runner produces:
    {"candidate": str, "job": str, "label": int, "dealbreaker": str | None,
     "scores": [int | None, ...]}   # one per repeat; None = no score came back
Pure functions with no AI calls, so they're tested exactly (tests/test_eval_scoring.py).
"""

from __future__ import annotations

import statistics

from evals.scoring.dataset import dealbreaker_cap


def prediction(record: dict) -> float | None:
    """The job's score across repeats: the median of the scores that came back."""
    got = [s for s in record["scores"] if s is not None]
    return statistics.median(got) if got else None


def _ranks(values: list[float]) -> list[float]:
    """Ranks from 1, ties sharing the average of their positions."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation: 1 = the same order, 0 = unrelated, -1 = reversed. None if it can't be computed."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    var = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return cov / var if var else None


def summarise(records: list[dict]) -> dict:
    """The headline numbers for a set of records (all of them, or one candidate's)."""
    scored = [(r, prediction(r)) for r in records]
    pairs = [(r["label"], p) for r, p in scored if p is not None]
    errors = [p - label for label, p in pairs]
    out: dict = {
        "jobs": len(records),
        "scored": len(pairs),
        "missing": sum(s is None for r in records for s in r["scores"]),  # job x repeat with no score
        "mae": round(statistics.fmean(abs(e) for e in errors), 2) if errors else None,
        "bias": round(statistics.fmean(errors), 2) if errors else None,  # + = scores higher than you
        "within_1": round(sum(abs(e) <= 1 for e in errors) / len(errors), 2) if errors else None,
        "exact": round(sum(e == 0 for e in errors) / len(errors), 2) if errors else None,
        "spearman": None,
    }
    rho = spearman([float(a) for a, _ in pairs], [float(b) for _, b in pairs])
    out["spearman"] = round(rho, 2) if rho is not None else None

    # Dealbreakers: labelled ones the AI let through, and good jobs it wrongly sank
    breakers = [(r, p) for r, p in scored if r["dealbreaker"] and p is not None]
    caught = [r for r, p in breakers if p <= dealbreaker_cap(r["dealbreaker"])]
    out["dealbreaker_recall"] = round(len(caught) / len(breakers), 2) if breakers else None
    out["dealbreakers_missed"] = [f"{r['candidate']}/{r['job']}" for r, p in breakers
                                  if p > dealbreaker_cap(r["dealbreaker"])]
    out["false_dealbreakers"] = [f"{r['candidate']}/{r['job']}" for r, p in scored
                                 if p is not None and not r["dealbreaker"] and r["label"] >= 5 and p <= 3]

    # Steadiness across repeats (only meaningful with --repeats 2 or more)
    spreads = [max(got) - min(got) for r in records if len(got := [s for s in r["scores"] if s is not None]) > 1]
    out["unstable"] = sum(s >= 2 for s in spreads)  # jobs whose repeats differ by 2 or more
    out["mean_spread"] = round(statistics.fmean(spreads), 2) if spreads else None
    return out


def biggest_misses(records: list[dict], n: int = 5) -> list[dict]:
    """The jobs furthest from their label, worst first: what to read when the numbers look off."""
    rows = [(abs(p - r["label"]), r, p) for r in records if (p := prediction(r)) is not None]
    rows.sort(key=lambda x: -x[0])
    return [{"case": f"{r['candidate']}/{r['job']}", "label": r["label"], "ai": p, "off_by": round(d, 1)}
            for d, r, p in rows[:n] if d >= 2]


def compare(old: dict, new: dict) -> list[str]:
    """Plain lines saying what changed between two runs' overall summaries."""
    better_if_lower = {"mae", "missing", "unstable", "mean_spread"}
    lines = []
    for key in ("mae", "within_1", "exact", "spearman", "dealbreaker_recall", "bias", "missing", "unstable"):
        a, b = old.get(key), new.get(key)
        if a is None or b is None or a == b:
            lines.append(f"{key:20} {a!s:>6} -> {b!s:>6}")
            continue
        if key == "bias":
            verdict = "better" if abs(b) < abs(a) else "worse"
        else:
            verdict = "better" if (b < a) == (key in better_if_lower) else "worse"
        lines.append(f"{key:20} {a!s:>6} -> {b!s:>6}  ({verdict})")
    return lines
