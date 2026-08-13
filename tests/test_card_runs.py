"""Per-card run records: held/final/kept, run scoping, and the played filter.

Uses synthetic runs so the expected numbers are stated rather than derived from
whatever save data happens to be on the machine.
"""

from sts2.analytics import _build_card_runs, compute_analytics
from sts2.models import RunFloor, RunHistory


def _run(character, win, deck, held=None, offered=(), picked=None):
    """One run. `held` defaults to the final deck, as it does for a run with no
    gains or removals."""
    floors = [RunFloor(floor=1, type="combat",
                       cards_offered=list(offered), card_picked=picked or "")]
    return RunHistory(id=f"{character}{win}{len(deck)}{picked or ''}",
                      character=character, win=win, deck=list(deck),
                      held=list(held if held is not None else deck), floors=floors)


def test_held_counts_cards_that_left_the_deck():
    """A card gained and later removed is in `held` but not the final deck."""
    runs = [_run("Ironclad", True, deck=["A"], held=["A", "B"])]
    rec = _build_card_runs(runs, {})

    assert rec["A"]["held_won"] == 1
    assert rec["A"]["final_won"] == 1
    assert rec["A"]["kept"] == 1
    # B was held but did not survive: it counts for held, not final or kept.
    assert rec["B"]["held_won"] == 1
    assert rec["B"]["final_won"] == 0
    assert rec["B"]["kept"] == 0


def test_starting_deck_is_seeded_per_character():
    """Starters are never recorded as a gain, so they must be seeded or a
    starter cut before the end vanishes from the run entirely."""
    starters = {"Ironclad": {"STRIKE"}}
    # A run that ended with no Strike at all — transformed away, say.
    runs = [_run("Ironclad", False, deck=["OTHER"], held=["OTHER"])]

    assert "STRIKE" not in _build_card_runs(runs, {})
    seeded = _build_card_runs(runs, starters)
    assert seeded["STRIKE"]["held_lost"] == 1
    assert seeded["STRIKE"]["kept"] == 0
    # ...and only for its own character.
    other = _build_card_runs([_run("Silent", False, deck=["OTHER"])], starters)
    assert "STRIKE" not in other


def test_offered_but_never_held_is_recorded_without_presence():
    """The distinction the "Played only" filter turns on: a skipped card has a
    record, but no presence."""
    runs = [_run("Defect", True, deck=["TAKEN"], offered=["TAKEN", "SKIPPED"],
                 picked="TAKEN")]
    rec = _build_card_runs(runs, {})

    assert rec["SKIPPED"]["offered"] == 1
    assert rec["SKIPPED"]["picked"] == 0
    assert rec["SKIPPED"]["held_won"] + rec["SKIPPED"]["held_lost"] == 0
    assert rec["TAKEN"]["picked"] == 1
    assert rec["TAKEN"]["held_won"] == 1


def test_per_character_records_sum_to_the_overall():
    """The invariant that makes run scoping trustworthy."""
    runs = [
        _run("Ironclad", True, deck=["A", "B"]),
        _run("Ironclad", False, deck=["A"]),
        _run("Defect", True, deck=["A"], offered=["A"], picked="A"),
        _run("Silent", False, deck=["B"]),
    ]
    out = compute_analytics(runs, {}, None)
    overall, by_char = out["card_runs"], out["card_runs_by_character"]

    assert set(by_char) == {"Ironclad", "Defect", "Silent"}
    for card_id, rec in overall.items():
        for field in rec:
            assert sum(by_char[c].get(card_id, {}).get(field, 0) for c in by_char) \
                == rec[field], f"{card_id}.{field} does not sum"
    # A is in all three characters' runs; B only in Ironclad and Silent.
    assert overall["A"]["held_won"] == 2 and overall["A"]["held_lost"] == 1
    assert "B" not in by_char["Defect"]


async def test_cards_run_scope_narrows_stats_not_the_card_list(client):
    """runs= must not change which cards are listed, only their numbers."""
    plain = await client.get("/cards?rarity=Event")
    scoped = await client.get("/cards?rarity=Event&runs=Defect")
    assert scoped.status_code == 200

    def count(text):
        import re
        return int(re.search(r"<h1>Cards \((\d+)\)</h1>", text).group(1))

    assert count(plain.text) == count(scoped.text)
    assert 'class="active"' in scoped.text


async def test_cards_run_scope_rejects_unknown_character(client):
    """An unrecognised value falls back to unscoped rather than emptying the
    page or 500ing."""
    resp = await client.get("/cards?rarity=Event&runs=NotACharacter")
    assert resp.status_code == 200
    plain = await client.get("/cards?rarity=Event")
    assert resp.text.count('class="card-tile"') == plain.text.count('class="card-tile"')


async def test_cards_played_filter_hides_cards_with_no_presence(client):
    """Every surviving tile must carry a Held figure — that is what the filter
    promises. Stated as an invariant rather than a count, because the fixture
    has no run history and the filter then correctly yields nothing; the
    counting is asserted against synthetic runs above."""
    import re
    resp = await client.get("/cards?played=1")
    assert resp.status_code == 200
    tiles = re.findall(
        r'class="gc-title">([^<]+)</p>.*?class="card-tile-meta">(.*?)</div>',
        resp.text, re.S)
    for name, meta in tiles:
        assert re.search(r"Held \d+/\d+", meta), f"{name} has no Held figure"

    unfiltered = await client.get("/cards")
    assert count_cards(resp.text) <= count_cards(unfiltered.text)


def count_cards(text):
    import re
    return int(re.search(r"<h1>Cards \((\d+)\)</h1>", text).group(1))


async def test_cards_played_filter_composes_with_run_scope(client):
    """Narrowing the scope can only narrow the played set."""
    everything = count_cards((await client.get("/cards?rarity=Event&played=1")).text)
    scoped = count_cards(
        (await client.get("/cards?rarity=Event&runs=Defect&played=1")).text)
    assert scoped <= everything


async def test_cards_filters_survive_in_every_link(client):
    """runs and played ride along in the filter links and the load-more URL.

    A character with no runs must stay selected rather than reverting to All
    runs — the scope is validated against the character list, not against which
    characters happen to have run history.
    """
    import re
    resp = await client.get("/cards?character=Ironclad&runs=Defect&played=1")
    assert resp.status_code == 200
    assert "runs=Defect" in resp.text
    assert "played=1" in resp.text

    nxt = re.search(r'data-next-url="([^"]*)"', resp.text)
    if nxt and nxt.group(1):
        assert "runs=Defect" in nxt.group(1)
        assert "played=1" in nxt.group(1)
