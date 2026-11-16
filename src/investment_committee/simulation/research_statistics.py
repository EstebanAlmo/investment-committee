"""The statistics the research protocol asks for
(docs/forex-research-protocol.md, sections 5 and 6), computed from the
trades a backtest study recorded. Standard library only.

  t_interval_95        95 % interval of a mean by Student's t
  bootstrap_interval_95  the same by resampling, with a fixed seed
  null_win_chance      chance of reaching the target before the stop on a
                       driftless walk from the mid price at entry
  binomial_upper_tail  one-sided exact binomial test
  research_summary     all of it for a set of trades, as run.json holds them

These are descriptions of a sample, in floating point: no money is
computed here and nothing is fed back into a strategy. Trades of two pairs
at the same hours are correlated, so every interval is optimistic.
"""

from __future__ import annotations

import math
import random
from datetime import datetime
from statistics import mean, stdev
from typing import Any, Mapping, Sequence

VALID_EXITS = ("OBJETIVO", "STOP")


def _incomplete_beta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b), by its continued fraction (Lentz)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _incomplete_beta(b, a, 1.0 - x)
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)) / a
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    result = d
    for m in range(1, 400):
        for numerator in (m * (b - m) * x / ((a + 2 * m - 1.0) * (a + 2 * m)), -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1.0))):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + numerator / c
            c = c if abs(c) > tiny else tiny
            result *= d * c
        if abs(d * c - 1.0) < 1e-14:
            break
    return front * result


def student_t_cdf(t: float, df: int) -> float:
    tail = 0.5 * _incomplete_beta(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t >= 0 else tail


def student_t_quantile(p: float, df: int) -> float:
    """The t with P(T <= t) = p, for p above one half, by bisection."""
    if not 0.5 < p < 1.0 or df < 1:
        raise ValueError("p must be between 0.5 and 1, with at least one degree of freedom")
    low, high = 0.0, 1000.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if student_t_cdf(middle, df) < p:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def t_interval_95(values: Sequence[float]) -> tuple[float, float, float] | None:
    """(mean, low, high) of the 95 % interval of the mean; None with fewer than two values."""
    n = len(values)
    if n < 2:
        return None
    centre, half = mean(values), student_t_quantile(0.975, n - 1) * stdev(values) / math.sqrt(n)
    return centre, centre - half, centre + half


def bootstrap_interval_95(values: Sequence[float], *, seed: int, resamples: int = 10000) -> tuple[float, float] | None:
    """Percentile interval of the mean over `resamples` resamples with
    replacement, drawn from random.Random(seed): the same numbers on every
    machine. The ends are the values at positions 2.5 % and 97.5 % of the
    sorted means."""
    n = len(values)
    if n < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(resamples))
    return means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]


def null_win_chance(stop_distance: float, target_distance: float, spread: float, slippage: float) -> float:
    """Chance of the target before the stop for a price without drift
    (protocol, section 5): down / (down + up), with down = D - slippage -
    spread and up = T + slippage + spread."""
    down, up = stop_distance - slippage - spread, target_distance + slippage + spread
    if down <= 0 or up <= 0:
        raise ValueError("the stop must be further than the costs, and the target positive")
    return down / (down + up)


def binomial_upper_tail(successes: int, trials: int, chance: float) -> float:
    """P(X >= successes) for X ~ Binomial(trials, chance): the one-sided exact test."""
    if not 0 <= successes <= trials or not 0.0 <= chance <= 1.0:
        raise ValueError("successes must be within the trials and the chance within 0 and 1")
    return min(1.0, sum(math.comb(trials, k) * chance**k * (1.0 - chance) ** (trials - k) for k in range(successes, trials + 1)))


def is_valid_closed(trade: Mapping[str, Any]) -> bool:
    return trade["status"] == "CERRADA" and trade["result"] is not None and trade["indeterminate"] is None


def _figures(trades: Sequence[Mapping[str, Any]], seed: int) -> dict[str, Any]:
    r = [float(t["result"]["r_multiple"]) for t in trades]
    out: dict[str, Any] = {"n": len(r), "wins": sum(x > 0 for x in r), "losses": sum(x < 0 for x in r),
                           "expectancy_r": mean(r) if r else None, "t_interval_95": None, "bootstrap_interval_95": None}
    interval = t_interval_95(r)
    if interval is not None:
        out["t_interval_95"] = [interval[1], interval[2]]
        out["bootstrap_interval_95"] = list(bootstrap_interval_95(r, seed=seed) or ())
    decided = [t for t in trades if t["result"]["reason"] in VALID_EXITS]
    chances = []
    for t in decided:
        p = t["proposal"]
        entry, stop, target = float(p["entry"]), float(p["stop"]), float(p["target"])
        chances.append(null_win_chance(abs(entry - stop), abs(target - entry), float(p["spread"]), float(p["slippage"])))
    targets = sum(t["result"]["reason"] == "OBJETIVO" for t in decided)
    out["stop_or_target"] = {
        "n": len(decided), "targets": targets, "target_rate": targets / len(decided) if decided else None,
        "null_rate": mean(chances) if chances else None,
        "p_value_above_null": binomial_upper_tail(targets, len(decided), mean(chances)) if decided else None,
    }
    return out


def research_summary(trades: Sequence[Mapping[str, Any]], *, start: datetime, end: datetime, seed: int) -> dict[str, Any]:
    """The protocol's figures for the valid closed trades among `trades`
    (run.json's records): all together, by pair, by direction, and by half
    of the calendar period from `start` to `end` (by the entry's instant)."""
    valid = [t for t in trades if is_valid_closed(t)]
    middle = start + (end - start) / 2
    first = [t for t in valid if datetime.fromisoformat(t["entry_at"]) < middle]
    scopes: dict[str, Sequence[Mapping[str, Any]]] = {"TOTAL": valid}
    for symbol in sorted({t["symbol"] for t in valid}):
        scopes[symbol] = [t for t in valid if t["symbol"] == symbol]
    for direction in sorted({t["direction"] for t in valid}):
        scopes[direction] = [t for t in valid if t["direction"] == direction]
    scopes["PRIMERA_MITAD"] = first
    scopes["SEGUNDA_MITAD"] = [t for t in valid if t not in first]
    return {"seed": seed, "middle": middle.isoformat(), "scopes": {name: _figures(chosen, seed) for name, chosen in scopes.items()}}
