"""The two-stage card model from ANALYSIS_LOG.md section 1.

Stage 1 fits a run-level baseline so a card's runs can be judged against
comparable runs rather than against the global win rate — without it, a card
ranking is mostly a rarity ranking (see DATA_GUIDE.md pitfall 1).

Stage 2 gives each card a log-odds shift on top of its runs' predicted
probabilities, with a Normal(0, tau^2) prior. tau is how much cards genuinely
differ; `fit_tau` estimates it and, at the time of writing, returns 0 — the
data cannot resolve the differences.

    import sys; sys.path.insert(0, "analysis")
    from card_model import baseline, fit_tau, card_effects

    base = baseline()
    tau, interior = fit_tau(base)          # check `interior` before trusting tau
    for row in card_effects(base, tau=0.35)[:10]:
        print(row)
"""
from __future__ import annotations

import math

import numpy as np
import statsmodels.api as sm

from sts2 import saves
from sts2.knowledge import KnowledgeBase

# Wide enough to contain any plausible per-card effect; `fit_tau` reports
# whether its optimum is interior, because an optimum on the boundary means the
# search range was wrong (or the prior was not normalised — see below).
_GRID = np.linspace(-4.0, 4.0, 401)


def baseline(runs=None):
    """Per-run predicted win log-odds from character, ascension and depth.

    Depth is `log(distinct cards held)`. It is both a confounder and a mediator
    (DATA_GUIDE.md pitfall 2): including it removes the survivorship artifact
    and clamps part of any real card effect, so every downstream estimate is
    biased toward zero. That is the conservative direction, deliberately.
    """
    runs = runs if runs is not None else saves.get_run_history()
    kb = KnowledgeBase()
    starters = {c.id for c in kb.cards if c.rarity == "Starter"}

    held = [set(r.held) for r in runs]     # held already contains deck
    chars = sorted({r.character for r in runs if r.character})
    X, y = [], []
    for r, h in zip(runs, held):
        row = [1.0] + [1.0 if r.character == c else 0.0 for c in chars[1:]]
        row += [r.ascension / 10.0, math.log(max(len(h), 1)) - 3.0]
        X.append(row)
        y.append(1.0 if r.win else 0.0)
    fit = sm.Logit(np.array(y), np.array(X)).fit(disp=0)
    names = ["const"] + [f"char[{c}]" for c in chars[1:]] + ["ascension/10", "log_depth"]
    return dict(runs=runs, held=held, starters=starters, fit=fit,
                names=names, logit_p=np.asarray(fit.fittedvalues))


def _per_card_loglik(base) -> dict[str, np.ndarray]:
    """Log-likelihood of each candidate effect, per card, over _GRID."""
    by_card: dict[str, list[int]] = {}
    for i, h in enumerate(base["held"]):
        for cid in h:
            if cid not in base["starters"]:      # starters are in every run
                by_card.setdefault(cid, []).append(i)
    wins = np.array([1.0 if r.win else 0.0 for r in base["runs"]])
    out = {}
    for cid, idx in by_card.items():
        if len(idx) < 3:
            continue
        lp = base["logit_p"][idx][:, None] + _GRID[None, :]
        y = wins[idx][:, None]
        out[cid] = (y * -np.logaddexp(0, -lp) + (1 - y) * -np.logaddexp(0, lp)).sum(0)
    return out


def fit_tau(base, lo: float = 0.02, hi: float = 2.0, n: int = 100):
    """Empirical-Bayes estimate of the between-card spread.

    Returns (tau, interior). **Check `interior`.** The prior must be a
    normalised density — dropping the 1/(tau*sqrt(2*pi)) factor makes the
    marginal likelihood rise monotonically in tau, which sent an earlier version
    to the upper boundary and produced a confident, entirely artifactual ranking
    (DATA_GUIDE.md pitfall 11).
    """
    LL = _per_card_loglik(base)
    grid = np.linspace(lo, hi, n)
    best, best_ll = None, -np.inf
    for tau in grid:
        prior = np.exp(-(_GRID ** 2) / (2 * tau * tau)) / (tau * math.sqrt(2 * math.pi))
        total = 0.0
        for ll in LL.values():
            m = ll.max()
            total += m + math.log(float((np.exp(ll - m) * prior).sum()))
        if total > best_ll:
            best_ll, best = total, tau
    return float(best), bool(grid[0] < best < grid[-1])


def card_effects(base, tau: float):
    """[(card_id, posterior mean log-odds, P(effect > 0), runs held)], best first.

    With tau fitted from the data this collapses to nothing, which is the honest
    answer. A hand-set tau (0.35 was used for the published ordering) asserts a
    spread the data does not evidence — legitimate, but label it as an
    assumption rather than a finding.
    """
    counts: dict[str, int] = {}
    for h in base["held"]:
        for cid in h:
            counts[cid] = counts.get(cid, 0) + 1

    prior = np.exp(-(_GRID ** 2) / (2 * tau * tau))
    rows = []
    for cid, ll in _per_card_loglik(base).items():
        w = np.exp(ll - ll.max()) * prior
        z = w.sum()
        rows.append((cid,
                     float((w * _GRID).sum() / z),
                     float(w[_GRID > 0].sum() / z),
                     counts.get(cid, 0)))
    return sorted(rows, key=lambda r: -r[1])
