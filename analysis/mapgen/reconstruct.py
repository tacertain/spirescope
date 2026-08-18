"""Recover which map nodes were *offered* on each run, not just which were walked.

The saves record only the path taken (DATA_GUIDE.md pitfall 3d). Regenerating the
map from the seed gives the whole graph, and the walked node types then pin down
which column was walked — the type sequence identifies a unique path in 85 of 89
runs. That yields offered-versus-taken data for every real decision.

    import sys; sys.path.insert(0, "analysis"); sys.path.insert(0, "analysis/mapgen")
    from reconstruct import load_maps, choice_points
    pts = choice_points(load_maps())

See mapgen/README.md for how maps-v0.107.1.jsonl is produced.
"""
from __future__ import annotations

import collections
import json
import pathlib

# The save's map_point_type vocabulary -> the engine's MapPoint.PointType names.
TYPE_MAP = {
    "monster": "Monster",
    "elite": "Elite",
    "rest_site": "RestSite",
    "shop": "Shop",
    "treasure": "Treasure",
    "unknown": "Unknown",
}

_HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_MAPS = _HERE / "maps-v0.107.1.jsonl"


def load_maps(path=None) -> dict[str, dict]:
    """{seed: record} for every successfully regenerated map."""
    out = {}
    for line in pathlib.Path(path or DEFAULT_MAPS).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if "rows" in rec.get("map", {}):
            out[rec["seed"]] = rec
    return out


def _graph(rec):
    nodes, kids = {}, {}
    for row in rec["map"]["rows"]:
        for n in row:
            key = (n["col"], n["row"])
            nodes[key] = n["type"]
            kids[key] = [(c["col"], c["row"]) for c in (n["children"] or [])]
    return nodes, kids


def walked_types(raw_run) -> list[str]:
    """Act-1 node types actually walked, Neow and the boss trimmed off.

    **Rows are 1-based and the boss sits at row 16**, so act-1 floor `f` is map
    row `f - 1`. Getting that offset wrong makes validation fail 88 of 89 runs
    while looking like a data problem.
    """
    acts = raw_run.get("map_point_history") or []
    if not acts:
        return []
    walked = [fd.get("map_point_type") for fd in acts[0]]
    body = walked[1:-1] if walked and walked[-1] == "boss" else walked[1:]
    return body


def reconstruct_path(rec, raw_run, max_paths: int = 5):
    """The walked path as [(col, row)], or None when it is not unique.

    Ambiguity is rare (1 of 89) but real: two columns of the same type in the
    same row leave the sequence under-determined.
    """
    want = [TYPE_MAP.get(t) for t in walked_types(raw_run)]
    if len(want) < 2 or any(w is None for w in want):
        return None
    nodes, kids = _graph(rec)
    paths = []

    def dfs(node, i, acc):
        if len(paths) > max_paths:
            return
        if i == len(want) - 1:
            paths.append(acc + [node])
            return
        for ch in kids.get(node, []):
            if nodes.get(ch) == want[i + 1]:
                dfs(ch, i + 1, acc + [node])

    for start in [k for k in nodes if k[1] == 1 and nodes[k] == want[0]]:
        dfs(start, 0, [])
    return paths[0] if len(paths) == 1 else None


def validate(maps, raw_by_seed) -> tuple[int, int]:
    """(consistent, checked) — every walked type must exist in its map row.

    Run this after any game update. It is the check that proves the regenerated
    maps are the ones the player actually saw.
    """
    consistent = checked = 0
    for seed, rec in maps.items():
        raw = raw_by_seed.get(seed)
        if not raw:
            continue
        nodes, _ = _graph(rec)
        by_row = collections.defaultdict(set)
        for (_col, row), t in nodes.items():
            by_row[row].add(t)
        body = walked_types(raw)
        ok = all(TYPE_MAP.get(t) in by_row.get(i, set())
                 for i, t in enumerate(body, start=1) if TYPE_MAP.get(t))
        checked += 1
        consistent += ok
    return consistent, checked


def choice_points(maps, raw_by_seed=None) -> list[dict]:
    """One row per real decision with two or more onward options.

    Fields: seed, variant, row, taken (type), options (types offered),
    elite_offered, took_elite. Nodes with a single child carry no information
    and are skipped.
    """
    if raw_by_seed is None:
        import extract  # analysis/extract.py
        raw_by_seed = {str(d.get("seed")): d for d in extract.raw_runs()}
    out = []
    for seed, rec in maps.items():
        raw = raw_by_seed.get(seed)
        if not raw:
            continue
        path = reconstruct_path(rec, raw)
        if path is None:
            continue
        nodes, kids = _graph(rec)
        for i, node in enumerate(path[:-1]):
            opts = kids.get(node, [])
            if len(opts) < 2:
                continue
            types = [nodes.get(c) for c in opts]
            taken = nodes.get(path[i + 1])
            out.append(dict(
                seed=seed, variant=rec["variant"], row=node[1],
                taken=taken, options=types,
                elite_offered="Elite" in types, took_elite=taken == "Elite",
            ))
    return out
