"""Data-integrity audit for the card data, art and run statistics.

Read-only. Run after `update`, after `extract-art`, and after upgrading the
game — the checks here are the ones that have actually caught real breakage,
each written as an invariant rather than a snapshot so they do not need editing
every patch.

    python scripts/health_check.py

Exits non-zero if any FAIL check trips. WARN lines are informational: they
report counts that legitimately change between game versions.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sts2 import cardart  # noqa: E402
from sts2.config import CHARACTERS, DATA_DIR, DUAL_SUFFIXES  # noqa: E402
from sts2.knowledge import KnowledgeBase  # noqa: E402

_failed = False


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failed
    if not ok:
        _failed = True
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def note(label: str, detail: str = "") -> None:
    print(f"  ....  {label}{'  — ' + detail if detail else ''}")


def main() -> int:
    kb = KnowledgeBase()
    raw = (DATA_DIR / "cards.json").read_text(encoding="utf-8")
    records = json.loads(raw)

    print("\ncards.json")
    check("serialises as indent=2 / ensure_ascii (hand edits must round-trip)",
          json.dumps(records, indent=2) + "\n" == raw)

    ids = {c["id"] for c in records}
    orphans = []
    for c in records:
        stem = c["id"].split(".", 1)[1]
        suf = next((s for s in DUAL_SUFFIXES if stem.endswith(s)), None)
        if suf and "CARD." + stem[: -len(suf)] not in ids:
            orphans.append(c["id"])
    # A suffixed id with no base never matches a save, which references the base
    # form only, and produces a "discovered" placeholder instead.
    check("no suffixed id lacks its base form", not orphans, ", ".join(orphans[:5]))

    empty_rarity = [c["id"] for c in records if not str(c.get("rarity", "")).strip()]
    note(f"{len(empty_rarity)} records with an empty rarity (stub records; see DESIGN.md)")

    # Stars are the Regent's currency and no other character's, so a star cost
    # anywhere else means a source crossed a column, not that the game changed.
    # The value check catches the other half: `StarCost = -1` arriving as a
    # literal "-1" in an orb, the mistake `_wiki_cost` already had once.
    starred = [c for c in records if str(c.get("star_cost", "")).strip()]
    stray = [c["id"] for c in starred if c.get("character") != "Regent"]
    check("star costs only on Regent cards", not stray, ", ".join(stray[:5]))
    bad_star = [f'{c["id"]}={c["star_cost"]}' for c in starred
                if c["star_cost"] != "X" and not c["star_cost"].isdigit()]
    check("every star cost is a number or X", not bad_star, ", ".join(bad_star[:5]))
    note(f"{len(starred)} cards charge Stars")

    print("\nknowledge base")
    names = collections.Counter(c.name for c in kb.cards)
    # Strike and Defend legitimately have one id per character.
    dupes = {n: k for n, k in names.items() if k > 1 and n not in ("Strike", "Defend")}
    check("no duplicate card names after alias filtering", not dupes, str(dupes))

    # Not a failure: a card the game added since the last `update` legitimately
    # shows up here until the data catches up. Persisting past an `update` means
    # cards.json has it under a different id, or not at all.
    placeholders = [c.id for c in kb.cards if c.source == "discovered"]
    if placeholders:
        note(f"{len(placeholders)} discovered placeholder(s) — in a run but not in "
             f"cards.json: {', '.join(placeholders[:5])}")
    else:
        note("no discovered placeholders")

    print("\ncard art")
    art = cardart.load_manifest()
    if not art:
        note("no manifest — run `spirescope extract-art`")
    else:
        missing = [c.id for c in kb.cards if c.id not in art]
        note(f"{len(missing)} of {len(kb.cards)} loaded cards without art "
             "(version skew, stub records, and any placeholder above)")

    print("\nrun history")
    from sts2 import saves
    runs = saves.get_run_history()
    if not runs:
        note("no run files found; skipping run-derived checks "
             "(is STS2_SAVE_DIR set to the parent of `history`?)")
        return 1 if _failed else 0

    from sts2.analytics import compute_analytics
    analytics = compute_analytics(runs, {}, kb)
    card_runs = analytics["card_runs"]
    by_char = analytics["card_runs_by_character"]
    note(f"{len(runs)} runs, {len(card_runs)} cards with a run record")

    if art:
        played_no_art = [cid for cid, v in card_runs.items()
                         if v["held_won"] + v["held_lost"] and cid not in art]
        check("every card that appears in a run has art", not played_no_art,
              ", ".join(played_no_art[:5]))

    # Every run of a character held that character's starting deck, by definition.
    runs_by_char = collections.Counter(r.character for r in runs)
    bad_starters = []
    for card in kb.cards:
        if card.rarity == "Starter" and card.character in runs_by_char:
            rec = card_runs.get(card.id, {})
            held = rec.get("held_won", 0) + rec.get("held_lost", 0)
            if held != runs_by_char[card.character]:
                bad_starters.append(f"{card.id} {held}!={runs_by_char[card.character]}")
    check("starter held count equals that character's run count", not bad_starters,
          ", ".join(bad_starters[:5]))

    # Run scoping is a lookup, so the per-character split must be exact.
    mismatched = []
    for cid, rec in card_runs.items():
        for field, total in rec.items():
            if sum(by_char[c].get(cid, {}).get(field, 0) for c in by_char) != total:
                mismatched.append(f"{cid}.{field}")
    check("per-character records sum to the overall", not mismatched,
          ", ".join(mismatched[:5]))

    # The ascension range filter sums (character, ascension) buckets rather than
    # recomputing, which is only sound if every run lands in exactly one bucket.
    # A run whose character or ascension changed shape would show up here as a
    # range that quietly disagrees with All runs.
    buckets = [b for by_asc in analytics["card_runs_by_scope"].values()
               for b in by_asc.values()]
    scope_mismatched = []
    for cid, rec in card_runs.items():
        for field, total in rec.items():
            if sum(b.get(cid, {}).get(field, 0) for b in buckets) != total:
                scope_mismatched.append(f"{cid}.{field}")
    check("(character, ascension) buckets sum to the overall", not scope_mismatched,
          ", ".join(scope_mismatched[:5]))

    # kept is the Final line's denominator; the tile relies on that to omit it.
    bad_kept = [cid for cid, v in card_runs.items()
                if v["kept"] != v["final_won"] + v["final_lost"]]
    check("kept equals the Final denominator", not bad_kept, ", ".join(bad_kept[:5]))

    unknown_chars = {r.character for r in runs} - set(CHARACTERS)
    if unknown_chars:
        note(f"runs from characters outside CHARACTERS: {sorted(unknown_chars)}")

    return 1 if _failed else 0


if __name__ == "__main__":
    code = main()
    print("\nFAILURES — see above\n" if code else "\nAll checks passed\n")
    raise SystemExit(code)
