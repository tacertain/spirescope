"""Per-card run records: held/final/kept, run scoping, and the played filter.

Uses synthetic runs so the expected numbers are stated rather than derived from
whatever save data happens to be on the machine.
"""

from sts2.analytics import _build_card_runs, compute_analytics, sum_card_runs
from sts2.models import RunFloor, RunHistory


def _run(character, win, deck, held=None, offered=(), picked=None, ascension=0):
    """One run. `held` defaults to the final deck, as it does for a run with no
    gains or removals."""
    floors = [RunFloor(floor=1, type="combat",
                       cards_offered=list(offered), card_picked=picked or "")]
    return RunHistory(id=f"{character}{win}{len(deck)}{picked or ''}a{ascension}",
                      character=character, win=win, deck=list(deck), ascension=ascension,
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


def _buckets(by_scope, character=None, asc_min=0, asc_max=99):
    """The bucket records a scope covers — the same walk /cards does."""
    return [record
            for char, by_ascension in by_scope.items()
            if character is None or char == character
            for ascension, record in by_ascension.items()
            if asc_min <= ascension <= asc_max]


def test_ascension_buckets_sum_to_the_overall():
    """The invariant the ascension range filter rests on: it sums buckets rather
    than recomputing, which is only sound if every run lands in exactly one."""
    runs = [
        _run("Ironclad", True, deck=["A"], ascension=0),
        _run("Ironclad", False, deck=["A", "B"], ascension=5),
        _run("Defect", True, deck=["A"], ascension=5),
        _run("Defect", True, deck=["B"], ascension=10),
    ]
    out = compute_analytics(runs, {}, None)
    overall, by_scope = out["card_runs"], out["card_runs_by_scope"]

    assert {(c, a) for c, by_asc in by_scope.items() for a in by_asc} == {
        ("Ironclad", 0), ("Ironclad", 5), ("Defect", 5), ("Defect", 10)}
    # The whole point: summing every bucket reproduces the unscoped record
    # exactly, so the default page and a full range cannot disagree.
    assert sum_card_runs(_buckets(by_scope)) == overall


def test_ascension_range_sums_only_the_buckets_it_covers():
    """A range is the sum of its buckets, and excludes the ones outside it."""
    runs = [
        _run("Ironclad", True, deck=["A"], ascension=0),
        _run("Ironclad", False, deck=["A", "B"], ascension=5),
        _run("Defect", True, deck=["A"], ascension=5),
        _run("Defect", True, deck=["B"], ascension=10),
    ]
    by_scope = compute_analytics(runs, {}, None)["card_runs_by_scope"]
    mid = sum_card_runs(_buckets(by_scope, asc_min=1, asc_max=5))

    # Both ascension-5 runs, one won and one lost, and neither endpoint run.
    assert mid["A"]["held_lost"] == 1 and mid["A"]["held_won"] == 1
    assert mid["B"]["held_lost"] == 1
    assert mid["B"]["held_won"] == 0, "the ascension 10 win must be excluded"

    # Narrowing to a single bucket leaves only that character's cards.
    just_ten = sum_card_runs(_buckets(by_scope, asc_min=10, asc_max=10))
    assert "A" not in just_ten and just_ten["B"]["held_won"] == 1


def test_ascension_range_composes_with_the_character_scope():
    """The two scopes intersect rather than one overriding the other."""
    runs = [
        _run("Ironclad", True, deck=["A"], ascension=5),
        _run("Defect", False, deck=["A"], ascension=5),
    ]
    by_scope = compute_analytics(runs, {}, None)["card_runs_by_scope"]
    both = sum_card_runs(_buckets(by_scope, "Ironclad", asc_min=4, asc_max=6))

    assert both["A"]["held_won"] == 1
    assert both["A"]["held_lost"] == 0, "the Defect run must not leak in"


def test_scope_run_counts_sum_to_the_overview():
    """The headline sample must agree with the overview, or the page reports a
    win rate over a different set of runs than the card figures use."""
    runs = [
        _run("Ironclad", True, deck=["A"], ascension=0),
        _run("Ironclad", False, deck=["A"], ascension=5),
        _run("Defect", True, deck=["B"], ascension=5),
    ]
    out = compute_analytics(runs, {}, None)
    counts = out["run_counts_by_scope"]

    total = sum(c["runs"] for by in counts.values() for c in by.values())
    wins = sum(c["wins"] for by in counts.values() for c in by.values())
    assert (total, wins) == (out["overview"]["total"], out["overview"]["wins"])

    # And a single bucket holds exactly its own runs.
    assert counts["Ironclad"][5] == {"runs": 1, "wins": 0}
    assert counts["Defect"][5] == {"runs": 1, "wins": 1}


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


async def test_cards_ascension_range_narrows_stats_not_the_card_list(client):
    """Like runs=, the range scopes the figures and not the listing."""
    plain = await client.get("/cards?rarity=Event")
    scoped = await client.get("/cards?rarity=Event&asc_min=3&asc_max=5")
    assert scoped.status_code == 200
    assert count_cards(plain.text) == count_cards(scoped.text)


async def test_cards_ascension_range_rides_along_and_the_form_restates_the_rest(client):
    """A GET form replaces the query string wholesale, so every other active
    filter has to be restated as a hidden input or it is silently dropped — and
    the range itself must not be, or the selects could never change it."""
    resp = await client.get("/cards?character=Ironclad&runs=Defect&asc_min=3&asc_max=5")
    assert resp.status_code == 200
    assert "asc_min=3" in resp.text and "asc_max=5" in resp.text

    assert '<input type="hidden" name="character" value="Ironclad">' in resp.text
    assert '<input type="hidden" name="runs" value="Defect">' in resp.text
    assert 'type="hidden" name="asc_min"' not in resp.text
    assert 'type="hidden" name="asc_max"' not in resp.text


async def test_cards_default_ascension_range_stays_out_of_urls(client):
    """The full range is the default, so it must not clutter every link."""
    resp = await client.get("/cards")
    assert resp.status_code == 200
    assert "asc_min=" not in resp.text
    assert "asc_max=" not in resp.text


async def test_cards_inverted_ascension_range_is_swapped_not_emptied(client):
    """min > max is a mis-click. Honouring it literally would empty the page and
    read as a broken filter."""
    inverted = await client.get("/cards?rarity=Event&asc_min=7&asc_max=2")
    ordered = await client.get("/cards?rarity=Event&asc_min=2&asc_max=7")
    assert inverted.status_code == 200
    assert count_cards(inverted.text) == count_cards(ordered.text)
    # The dropdowns show the corrected order, not what was asked for.
    assert 'value="2" selected' in inverted.text
    assert 'value="7" selected' in inverted.text


async def test_cards_reports_the_sample_behind_the_figures(client):
    """The win rate is a headline for the scoped runs, so it has to state its
    denominator — a rate without one is the thing this codebase keeps rejecting."""
    resp = await client.get("/cards")
    assert resp.status_code == 200
    assert "Win Rate" in resp.text
    assert "Runs Won" in resp.text


async def test_cards_win_rate_is_blank_rather_than_zero_with_no_runs(client):
    """0% claims every run in scope was lost. "None matched" is a different and
    much weaker statement, and it is the true one when the scope is empty."""
    resp = await client.get("/cards?asc_min=9&asc_max=10")
    assert resp.status_code == 200
    assert "No runs match" in resp.text
    assert "0.0%" not in resp.text


def _tile_names(text):
    import re
    return re.findall(r'class="gc-title">([^<]+)</p>', text)


async def test_cards_alphabetical_sort_is_actually_alphabetical(client):
    resp = await client.get("/cards?sort=name")
    assert resp.status_code == 200
    names = [n.lower() for n in _tile_names(resp.text)]
    assert names == sorted(names)


def test_cost_sort_key_handles_the_non_numeric_costs():
    """Costs are strings, so a plain sort puts "12" before "2", "?" before "0"
    and "Unplayable" between "3" and "4"."""
    from types import SimpleNamespace

    from sts2.routes import _sort_key_cost

    key = _sort_key_cost({})
    cards = [SimpleNamespace(name=n, cost=c) for n, c in [
        ("twelve", "12"), ("two", "2"), ("zero", "0"), ("query", "?"),
        ("unplayable", "Unplayable"), ("also-zero", "0"), ("blank", ""),
    ]]
    assert [c.name for c in sorted(cards, key=key)] == [
        "also-zero", "zero",      # numeric ascending, name breaking the tie
        "two", "twelve",          # 2 before 12, which a string sort reverses
        "query",                  # "?" after every real cost
        "unplayable",
        "blank",                  # unknown, last
    ]


async def test_cards_cost_sort_orders_numerically_on_the_page(client):
    """Re-derived from the rendered tiles, because a sort that looks wired up and
    moves nothing is this control's known failure mode. Unplayable cards show no
    cost orb at all, so only the visible costs are checked."""
    import re
    resp = await client.get("/cards?sort=cost")
    assert resp.status_code == 200
    costs = [int(c) for c in re.findall(r'class="gc-cost">(\d+)</p>', resp.text)]
    assert costs, "no costs rendered"
    assert costs == sorted(costs)


async def test_cards_default_sort_is_win_rate(client):
    """No sort= must render exactly what sort=winrate does, and must not put the
    default back into the URLs it builds."""
    default = await client.get("/cards?rarity=Rare")
    explicit = await client.get("/cards?rarity=Rare&sort=winrate")
    assert _tile_names(default.text) == _tile_names(explicit.text)
    # The sort row links to the other two, so "sort=" appears — but never the
    # default, which would ride along in every filter link for no reason.
    assert "sort=winrate" not in default.text
    assert "sort=name" in default.text and "sort=cost" in default.text


async def test_cards_unknown_sort_falls_back_to_the_default(client):
    """?sort=pickrate is a live bookmark — it went with the pick stats. It must
    render the default order rather than an unsorted page or a 400."""
    stale = await client.get("/cards?rarity=Rare&sort=pickrate")
    default = await client.get("/cards?rarity=Rare")
    assert stale.status_code == 200
    assert _tile_names(stale.text) == _tile_names(default.text)


async def test_cards_sort_order_is_total(client):
    """Every key ends in the name, so no two cards can tie into an arbitrary
    position — the old fallback was the order of cards.json, which is only
    mostly alphabetical."""
    for sort in ("winrate", "name", "cost"):
        resp = await client.get(f"/cards?rarity=Rare&sort={sort}")
        again = await client.get(f"/cards?rarity=Rare&sort={sort}")
        assert _tile_names(resp.text) == _tile_names(again.text)


async def test_cards_ascension_range_is_bounded(client):
    """Out-of-range values are rejected by validation rather than reaching the
    bucket sum, where they would silently match nothing."""
    assert (await client.get("/cards?asc_min=-1")).status_code == 422
    assert (await client.get("/cards?asc_max=99")).status_code == 422
