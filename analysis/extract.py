"""Dataset builders for the run-history analyses.

The app's parser (`sts2.saves`) is lossy — see DATA_GUIDE.md — so everything
that needs a per-decision field reads the raw `.run` JSON instead. These are the
extractions the analyses in ANALYSIS_LOG.md were built on, kept so the figures
can be reproduced rather than re-derived.

Read-only. Import from a `python_run` snippet:

    import sys; sys.path.insert(0, "analysis")
    from extract import elite_encounters, rest_decisions, hazard_bands
"""
from __future__ import annotations

import collections
import glob
import json
import statistics

from sts2.config import SAVE_DIR

# player_stats carries one entry per player. 147 of 148 runs are solo; the one
# two-player run is taken from player 0's perspective like the rest.
_PLAYER = 0


def raw_runs(skip_abandoned: bool = False) -> list[dict]:
    """Every run file as a dict, newest last.

    `was_abandoned` marks a run quit rather than lost — 3 of 148. Excluding them
    does not move any published figure, but a quit is not a death, so any new
    survival analysis should pass skip_abandoned=True.
    """
    out = []
    for fn in sorted(glob.glob(str(SAVE_DIR / "history" / "*.run"))):
        d = json.load(open(fn, encoding="utf-8"))
        if skip_abandoned and d.get("was_abandoned"):
            continue
        out.append(d)
    return out


def flat_floors(d: dict) -> list[tuple[str, dict, dict]]:
    """(map_point_type, player_stats, room) per floor, acts flattened, in order.

    The encounter identity lives on the ROOM as `model_id` — not `encounter_id`,
    and not on the floor. `sts2.saves` surfaces it as `RunFloor.encounter`.
    """
    out = []
    for act in d.get("map_point_history", []):
        for fd in act:
            ps = fd.get("player_stats") or [{}]
            rooms = fd.get("rooms") or [{}]
            out.append((fd.get("map_point_type", "?"),
                        ps[_PLAYER] if len(ps) > _PLAYER else {},
                        rooms[0] if rooms else {}))
    return out


def _fatal(index: int, floors: list, win: bool) -> bool:
    """A floor is fatal when it is the last one recorded and the run was lost.

    `killed_by_encounter` is NOT usable for this: it is populated on winning
    runs too, so its presence does not mean the run ended there.
    """
    return index == len(floors) - 1 and not win


def elite_encounters(skip_abandoned: bool = False) -> list[dict]:
    """One row per elite fight, with the HP you walked in on.

    arrival HP comes from the *previous* floor, because a rest floor's own
    current_hp is recorded post-heal (see DATA_GUIDE.md).
    """
    rows = []
    for d in raw_runs(skip_abandoned):
        win = bool(d.get("win"))
        fl = flat_floors(d)
        for i, (t, ps, room) in enumerate(fl):
            if t != "elite":
                continue
            prev = fl[i - 1][1] if i else {}
            if not prev.get("max_hp"):
                continue
            rows.append(dict(
                enc=str(room.get("model_id", "")),
                floor=i + 1,
                hp=prev["current_hp"] / prev["max_hp"],
                asc=d.get("ascension", 0),
                fatal=_fatal(i, fl, win),
            ))
    return rows


def rest_decisions(skip_abandoned: bool = False) -> list[dict]:
    """One row per rest site that is the LAST rest before an elite or boss.

    "Last before" means walking forward from the rest site the next elite/boss
    is reached without passing another rest site. `rest_site_choices` is raw-only
    and is a list — the tent relic lets both HEAL and SMITH appear.
    """
    rows = []
    for d in raw_runs(skip_abandoned):
        win = bool(d.get("win"))
        fl = flat_floors(d)
        for i, (t, ps, _room) in enumerate(fl):
            if t != "rest_site" or not ps.get("rest_site_choices") or not ps.get("max_hp"):
                continue
            nxt = None
            for j in range(i + 1, len(fl)):
                if fl[j][0] == "rest_site":
                    break
                if fl[j][0] in ("elite", "boss"):
                    nxt = j
                    break
            if nxt is None:
                continue
            ch = set(ps["rest_site_choices"])
            rows.append(dict(
                choice=("BOTH" if {"HEAL", "SMITH"} <= ch else
                        "HEAL" if "HEAL" in ch else
                        "SMITH" if "SMITH" in ch else "OTHER"),
                # The decision is made BEFORE the heal lands.
                pre_hp=(ps["current_hp"] - ps.get("hp_healed", 0)) / ps["max_hp"],
                kind=fl[nxt][0],
                asc=d.get("ascension", 0),
                died=_fatal(nxt, fl, win),
                won=win,
            ))
    return rows


def hazard_bands(bands=((1, 5), (6, 10), (11, 15), (16, 20), (21, 25), (26, 30),
                        (31, 35), (36, 40), (41, 45), (46, 50)),
                 skip_abandoned: bool = False) -> list[tuple]:
    """(band, entered, died, hazard) — deaths conditioned on reaching the band.

    A raw death count is confounded by how many runs get that far; this is the
    conditional version and the only honest way to say where runs die.
    """
    reach, die = collections.Counter(), collections.Counter()
    for d in raw_runs(skip_abandoned):
        fl = flat_floors(d)
        for f in range(1, len(fl) + 1):
            reach[f] += 1
        if not d.get("win") and fl:
            die[len(fl)] += 1
    out = []
    for lo, hi in bands:
        n = reach[lo]
        k = sum(die[f] for f in range(lo, hi + 1))
        if n:
            out.append((f"{lo}-{hi}", n, k, k / n))
    return out


def balance(rows: list[dict], group_key: str, covariates: list[str]) -> dict:
    """One-way ANOVA F per covariate across groups.

    Run this BEFORE any between-group comparison. F near 1 means the groups are
    comparable; a large F means they are not, and the contrast is measuring the
    covariate. This check is what caught a "late-act elites" group that silently
    mixed a floor-28 pool with a floor-44 one (F = 432.9 on floor).
    """
    out = {}
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r[group_key]].append(r)
    for cov in covariates:
        gs = [[r[cov] for r in g] for g in groups.values() if len(g) > 1]
        n = sum(len(g) for g in gs)
        if len(gs) < 2 or n <= len(gs):
            continue
        gm = sum(sum(g) for g in gs) / n
        ssb = sum(len(g) * (statistics.mean(g) - gm) ** 2 for g in gs)
        ssw = sum((v - statistics.mean(g)) ** 2 for g in gs for v in g)
        out[cov] = (ssb / (len(gs) - 1)) / (ssw / (n - len(gs))) if ssw else float("inf")
    return out
