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
import os
import statistics

from sts2.config import SAVE_DIR

# player_stats carries one entry per player. 147 of 148 runs are solo; the one
# two-player run is taken from player 0's perspective like the rest.
_PLAYER = 0

# Elites are drawn from a per-act pool, and comparing across pools measures act
# difficulty rather than elite difficulty — the error that cost two rounds of
# analysis (DATA_GUIDE.md pitfall 3). These are the pools observed so far,
# grouped by where their floor distributions actually coincide. Re-derive rather
# than trust if the game adds elites: group by median floor, then check
# `balance(rows, "enc", ["floor"])` comes back near 1.
ELITE_POOLS = {
    # Act 1 has TWO disjoint pools, one per act variant. Floor range does not
    # separate them — both sit at 7-15 — so grouping by floor alone silently
    # merges them. Co-occurrence does: `elite_cooccurrence()` shows exactly
    # zero shared runs across the A/B boundary, against 12-38 within each.
    "act1_overgrowth": ["ENCOUNTER.BYGONE_EFFIGY_ELITE",
                        "ENCOUNTER.BYRDONIS_ELITE",
                        "ENCOUNTER.PHROG_PARASITE_ELITE"],
    "act1_underdocks": ["ENCOUNTER.PHANTASMAL_GARDENERS_ELITE",
                        "ENCOUNTER.SKULKING_COLONY_ELITE",
                        "ENCOUNTER.TERROR_EEL_ELITE"],
    "act2": ["ENCOUNTER.DECIMILLIPEDE_ELITE",             # floors 24-33
             "ENCOUNTER.ENTOMANCER_ELITE",                # single pool: these
             "ENCOUNTER.INFESTED_PRISMS_ELITE"],          # do co-occur
    "act3": ["ENCOUNTER.KNIGHTS_ELITE",                   # floors 40-47
             "ENCOUNTER.MECHA_KNIGHT_ELITE",              # single pool
             "ENCOUNTER.SOUL_NEXUS_ELITE"],
}

# Which act-1 variant draws which pool. Mapping is exact: 60 Overgrowth runs met
# a pool-A elite and no Underdocks run did, and vice versa for 73 runs.
ACT1_VARIANT_POOL = {"ACT.OVERGROWTH": "act1_overgrowth",
                     "ACT.UNDERDOCKS": "act1_underdocks"}


def raw_runs(skip_abandoned: bool = False) -> list[dict]:
    """Every run file as a dict, **oldest first**.

    NOTE the order. `saves.get_run_history()` returns runs **newest first**, so
    the two lists are exact reverses of each other. `zip(parsed, raw_runs())`
    pairs the first run with the last and produces confident nonsense. Use
    `raw_by_id()` to join instead.

    `was_abandoned` marks a run quit rather than lost — 3 of 148. Excluding them
    does not move any published figure, but a quit is not a death, so any new
    survival analysis should pass skip_abandoned=True.
    """
    out = []
    for fn in sorted(glob.glob(str(SAVE_DIR / "history" / "*.run"))):
        d = json.load(open(fn, encoding="utf-8"))
        if skip_abandoned and d.get("was_abandoned"):
            continue
        out.append((os.path.basename(fn)[:-len(".run")], d))
    return [d for _, d in out]


def raw_by_id(skip_abandoned: bool = False) -> dict[str, dict]:
    """{RunHistory.id: raw run dict} — the safe way to join parsed against raw.

    `RunHistory.id` is the `.run` filename stem, unique across all 148 runs.
    Joining on it (or on `seed`) is correct; joining on list position is not,
    because the two sources are ordered oppositely.
    """
    out = {}
    for fn in sorted(glob.glob(str(SAVE_DIR / "history" / "*.run"))):
        d = json.load(open(fn, encoding="utf-8"))
        if skip_abandoned and d.get("was_abandoned"):
            continue
        out[os.path.basename(fn)[:-len(".run")]] = d
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


def card_offers(skip_abandoned: bool = False) -> list[dict]:
    """One row per card shown on a reward screen — the offer-as-instrument data.

    Shop screens are excluded from both sides: buying does not set was_picked
    (8 of 2063 do), so any pick rate including them is wrong. Event grants are
    unrecoverable and absent entirely.

    The offer is the one genuinely randomised event in a run, which is what
    makes this the only clean causal handle available — and compliance is ~19%,
    which is what makes it underpowered anyway. See ANALYSIS_LOG.md section 2.
    """
    rows = []
    for d in raw_runs(skip_abandoned):
        win = bool(d.get("win"))
        seed = d.get("seed", "")
        for t, ps, _room in flat_floors(d):
            if t == "shop":
                continue
            choices = ps.get("card_choices") or []
            for cc in choices:
                cid = (cc.get("card") or {}).get("id", "")
                if not cid:
                    continue
                rows.append(dict(card=cid, run=seed, won=win,
                                 taken=bool(cc.get("was_picked")),
                                 asc=d.get("ascension", 0)))
    return rows


def elite_cooccurrence() -> dict[tuple[str, str], int]:
    """{(elite_a, elite_b): runs containing both} — how to find a draw pool.

    Two elites in the same pool turn up in the same run; elites in disjoint
    pools never do. This is the *only* reliable way to separate act 1's two
    pools, because both occupy floors 7-15 and so look like one group under any
    floor-based test. Run it before trusting `ELITE_POOLS` after a game update.
    """
    pairs: dict[tuple[str, str], int] = collections.Counter()
    for d in raw_runs():
        seen = sorted({room.get("model_id", "") for t, _ps, room in flat_floors(d)
                       if t == "elite" and room.get("model_id")})
        for i, a in enumerate(seen):
            for b in seen[i + 1:]:
                pairs[(a, b)] += 1
    return dict(pairs)


def verify_assumptions() -> list[tuple[str, bool, str]]:
    """Re-check the data-shape claims DATA_GUIDE.md relies on.

    These are properties of the save format, not of the analysis, so a game
    patch can falsify them silently and every downstream number would still
    look plausible. Run this after upgrading the game. Same spirit as
    scripts/health_check.py.
    """
    from sts2 import saves

    out = []
    runs = saves.get_run_history()

    bad = [r.id for r in runs if not set(r.deck) <= set(r.held)]
    out.append(("held is a superset of deck", not bad,
                f"{len(bad)} runs violate it" if bad else f"all {len(runs)} runs"))

    pre = post = 0
    for d in raw_runs():
        fl = flat_floors(d)
        for i, (t, ps, _r) in enumerate(fl):
            if t != "rest_site" or not ps.get("hp_healed") or not i:
                continue
            prev = fl[i - 1][1]
            if not prev.get("max_hp"):
                continue
            pre += ps["current_hp"] == prev["current_hp"]
            post += ps["current_hp"] == prev["current_hp"] + ps["hp_healed"]
    out.append(("rest-site current_hp is post-heal", pre == 0 and post > 0,
                f"{post} post-heal, {pre} pre-heal"))

    named = sum(1 for r in elite_encounters() if r["enc"])
    total = len(elite_encounters())
    out.append(("elite encounters resolve a name", named == total and total > 0,
                f"{named}/{total} via rooms[0]['model_id']"))

    # Everything here numbers floors by position in the flattened history, so
    # this identity is load-bearing for hazard_bands and elite_encounters.
    raw = raw_by_id()
    bad_idx = joined = 0
    for r in runs:
        d = raw.get(r.id)
        if d is None:
            continue
        joined += 1
        fl = flat_floors(d)
        if len(fl) != len(r.floors) or any(pf.floor != i + 1
                                           for i, pf in enumerate(r.floors)):
            bad_idx += 1
    out.append(("RunFloor.floor == 1-based index into flat_floors",
                bad_idx == 0 and joined == len(runs),
                f"{joined - bad_idx}/{len(runs)} runs agree"))

    # Only pools that are ALTERNATIVES to each other must be disjoint — the two
    # act-1 variants. Pools from different acts co-occur constantly, because a
    # run passes through every act; checking those too was the first version of
    # this and it failed on 45 perfectly legitimate pairs.
    co = elite_cooccurrence()
    groups: dict[str, list[str]] = {}
    for pool in ELITE_POOLS:
        groups.setdefault(pool.split("_")[0], []).append(pool)
    bad_pairs = 0
    for alts in groups.values():
        for i, pa in enumerate(alts):
            for pb in alts[i + 1:]:
                for a in ELITE_POOLS[pa]:
                    for b in ELITE_POOLS[pb]:
                        if co.get(tuple(sorted((a, b))), 0):
                            bad_pairs += 1
    n_alt = sum(len(a) - 1 for a in groups.values())
    out.append(("alternative elite pools never co-occur", bad_pairs == 0,
                f"{bad_pairs} bad pair(s) across {n_alt} alternative-pool split(s)"))

    # Informational, not a check: one multiplayer run is a known and accepted
    # condition, and a check that always fails is a check people stop reading.
    multi = sum(1 for d in raw_runs() if len(d.get("players") or []) > 1)
    out.append((f"note: {multi} multiplayer run(s), extractions use player 0",
                True, "revisit if this grows"))

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
