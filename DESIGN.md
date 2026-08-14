# Design notes

Why parts of this codebase are the way they are, concentrating on the decisions
that are expensive to rediscover. Mechanics you can read off the code are left
out; what is here is the reasoning, the measurements behind it, and the traps.

Two subsystems have most of the non-obvious design: **card art**, which rebuilds
the game's own card from its archive, and **card statistics**, which reconciles
two different records of what you played.

---

## Running it

`STS2_SAVE_DIR` must point at the **parent of `history`** — `config.py` appends
`history` itself, so aiming at `history` fails silently. The game install
auto-detects separately; that attribute is `config.GAME_INSTALL_DIR`.

**Auto-detection is not a fallback that fails — it usually works, and that
matters.** It looks in `%APPDATA%\SlayTheSpire2\...`, which is a different path
from the Steam Cloud one `start-spirescope.cmd` sets. An earlier version of this
document implied the former was the wrong place. On a machine that has played
the game, both hold the same runs — measured here, the 142 `*.run` files are
**byte-identical** across the two. The file *counts* differ (193 vs 142) only
because the `%APPDATA%` copy also keeps 51 `*.run.backup` files, which the
loader ignores: `saves.py` globs `*.run`.

Two things follow, and both have caused wrong conclusions:

- **`STS2_SAVE_DIR` is not required to see run data.** Code run without it still
  finds a full save. `start-spirescope.cmd` sets it to pin the Steam Cloud copy,
  not to make run history work at all.
- **Testing `os.environ.get("STS2_SAVE_DIR")` is not a test for "runs are
  available."** A diagnostic script that guarded on the variable silently
  skipped its run-history section while 142 runs sat there for the taking. Ask
  `get_run_history()` instead. This is also why the HTTP tests quietly read real
  runs — see Testing this.

```bash
pip install -e ".[art]"     # the art extra is Pillow + texture2ddecoder
spirescope extract-art      # ~6s, writes to %APPDATA%\SpireScope\cardart
```

**Nothing extracted is bundled or committed.** The art is Mega Crit's;
extraction reads the user's own install and writes to their state dir, mirroring
what `localize.py` already does with card text. Keep it that way. Kreon is
fetched from Google Fonts under the OFL, so it *is* safe to commit, and is
recorded in `THIRD_PARTY_NOTICES.md`.

The app loads `manifest.json` at startup and exposes `card_art`, `card_sprites`,
`card_rules` and `card_text_len` to templates. Tiles ask the manifest for a
filename, so a tile can never link an image that is not on disk. The `/cardart`
mount only happens when the directory exists — pointing `StaticFiles` at a
missing path raises at startup and would break the app for anyone who had never
run the command.

---

## Card art extraction (`cardart.py`)

Reads the game's Godot `.pck`, reusing the archive index walk `localize.py`
already had. Nothing in the archive is encrypted.

### Texture formats

Atlases are `.ctex`: a 52-byte header, then either raw block data or a
length-prefixed image. Three cases matter — BC7 and BC3 (block formats, decoded
with `texture2ddecoder`) and PNG/WebP (`data_format` 1/2, which Pillow reads
directly). The small standalone UI textures ship as WebP; the card and UI
atlases are BC7; the `compressed_` atlas holding the Ancient template is BC3.

### `margin` is not decoration

The packer trims transparent edges and records what it took in
`margin = Rect2(x, y, w, h)`: `(x, y)` is where the trimmed region sits inside
the original image, `(w, h)` is how much came off in total, so the real texture
is `region.size + margin.size`. **Sprites are positioned by their original
bounds**, so pasting a region back without its margin puts it in the wrong
place. `_restore_margin` pastes every region onto a full-size transparent
canvas. 9 UI sprites and 34 card portraits carry one.

This cost real time once: `card_banner.tres` is trimmed 23px at the top, which
lifted the whole ribbon clear of the title. The symptom presented as *the title
is too low* when in fact the banner was too high.

The check that it is right: the three `ancient_card_text_bg_*` panels have very
different regions and *identical* 693x533 full sizes.

### Colour is baked at extraction, not in CSS

The archive holds **one** frame sprite per shape, in red, and **one** banner and
plaque, in teal. Colour comes from a ShaderMaterial running `shaders/hsv.gdshader`.

CSS `hue-rotate` looks like a free match and is not one — different luminance
weights, opposite rotation sense. A Rare banner came out **pink instead of gold**.
Every step of that shader is linear, so `cardart.py` folds the whole thing into
one 3x3 matrix and Pillow applies it once per tint at extraction. Exact colours,
no render cost. **Do not "simplify" this back to a CSS filter.**

Which node takes which material is not the split it looks like: **Frame takes
the character colour; PortraitBorder, TitleBanner and the type plaque take the
rarity colour.**

The character-to-frame-colour mapping is **inferred**, not read from the archive
— the game resolves it in compiled C#. It is `_FRAME_COLOR_BY_CHARACTER`. If a
character's frame looks wrong, that table is the place to fix it.

The type plaque comes from `card_portrait_border_plaque2.png`, a standalone
WebP texture, **not** the `card_portrait_border_plaque_s` region in the ui
atlas. `card.tscn` points at the former; using the latter gave a teal plaque
where the game's is grey-tinted by rarity. Whole textures like this are listed
in `UI_TEXTURES` and keyed by basename, because Godot's import step discards the
source folder.

### Cards whose art depends on a run-time choice

`_ART_VARIANTS` maps a card id to one face when the archive has several and none
under the bare name. Only Mad Science today: the Tinker Time event picks a card
type, so the archive holds `mad_science_attack`, `_skill` and `_power`.

The catalogue has no run to read, so it shows the first face in the game's own
`choose(Attack|Skill|Power)` order. **The per-copy truth is recoverable** — each
deck entry carries `props.ints.TinkerTimeType`, where **1/2/3 = Attack/Skill/
Power**, confirmed against remembered play. A run or deck view could show the
right face; the cards list cannot.

`TinkerTimeRider` selects the bonus effect from the eight the template lists
(Sapping, Choking, Energized, Wisdom, Chaos, Expertise, Curious, Improvement).
Observed values are 5, 7 and 9 — **the mapping is not confirmed**, and 9 exceeds
eight riders unless `Violence`, the attack-only modifier, is one of them. Do not
rely on it without checking.

More generally, `saves.py` reads only `id` from a deck entry and **drops `props`
entirely**. That field carries per-copy state across 40+ names — `SpoilsActIndex`
on every Spoils Map, Genetic Algorithm's damage counters, relic counters like
`TimesLifted` and `RewardsSacrificed`. Nothing depends on it today; it is where
to look if per-copy detail is ever wanted.

---

## Card rendering (`_card_tiles.html`, `style.css`)

Geometry comes from `scenes/cards/card.tscn`, converted to percentages of the
300x422 card. The card origin is `(150, 211)`, so a node at `offset_left -125`
sits at `(150-125)/300 = 8.333%`. A node's `stretch_mode` maps onto
`object-fit`: `5` (KEEP_ASPECT_CENTERED) is `contain`, `6` (KEEP_ASPECT_COVERED)
is `cover`. Layer order follows the scene tree.

Card text is HTML, not baked into the image, so it stays selectable, searchable
and translated.

### Fonts — read `card.tscn`, not old comments

**Kreon is the only face on a card.** Spectral was used for rules text and was
simply wrong: it is wider, so "Procure a random potion." wrapped where the game
fits it on one line. It has been removed from the repo.

| Node | Font | Size |
| --- | --- | --- |
| `DescriptionLabel` | `kreon_regular`, `line_separation = -3` | 21 (7cqw) |
| `TitleLabel` | `kreon_regular` + `spacing_glyph = 1` | 26 (8.667cqw) |
| `TypeLabel` | `kreon_bold` | 16 (5.333cqw) |
| `EnergyLabel` | `kreon_bold` | 32 (10.667cqw) |

`.gc-title` is deliberately Kreon **700 with no letter-spacing**, contradicting
that table. The game gets the title's weight from a heavy outline
(`outline_size = 12`, `font_outline_color #4D4B40`), not from the face, and the
CSS ring is nothing like that thick. Dropping to 400 without rebuilding the
outline looks thin. **Fix both together or neither.**

Rules text steps down in two size buckets by length. This is load-bearing: the
longest English card (122 chars) overflows the text box at full size, and the
`xlong` bucket leaves headroom for translations, which run longer.

Keywords are highlighted from each card's own `keywords` list, matched
**case-sensitively** with an optional inflection suffix. Case matters because
game terms are capitalised in the text and the same word lowercase is prose:
"Whenever a card is Exhausted" is a keyword, "draw 1 card" is not.

### Ancient cards are a different layout, not a recolour

`card.tscn` carries `AncientPortrait`, `AncientBorder` and `AncientTextBg` as
`visible = false` nodes that C# switches on, hiding the frame and portrait ring
in exchange. Ancient art is **250x351** — the whole card — where every other
card is a 250x190 portrait. Banner, plaque, title and rules text keep their
normal geometry.

`card_frame_ancient_s` is **not** a frame. The `card_frame_*_s` sprites are the
`Shadow` node's silhouettes, modulated to black at 25%. Drawing one as a frame
is what made these cards look washed-out grey.

`AncientBorder` blends additively (`blend_mode = 1`), which is
`mix-blend-mode: plus-lighter`. `AncientTextBg` ships grey and is modulated to
black, which is `filter: brightness(0)` since that leaves alpha alone.

**Two nodes are deliberately not transcribed literally.** Both are cases where a
tiny asymmetry is invisible in game against a dark table but obvious here on
light parchment:

- `AncientPortrait` works out to `99.667% x 99.763%` — 299x421 inside a 300x422
  card — leaving the right and bottom card edges uncovered. The page showed
  through as a white rim that *vanished on hover*, because the tile's
  `scale(1.02)` covers exactly that shortfall. It is `0, 0, 100% x 100%`.
- `AncientBorder`'s offsets are a 306x440 box centred at `(-1, -3)`, giving a
  9px halo top and bottom against 3px at the sides. The glow is inset a uniform
  7px inside its own sprite, so all the asymmetry came from the box. It is
  centred with an even 3px halo. **Verify by measuring: all four overhangs
  should be equal.**

Corners use `border-radius: 6.67cqw`, measured off `ancient_portrait_mask_large`.
**Do not use that mask as a CSS `mask-image`.** `card.tscn` never references it,
so its sizing is guesswork, and it carries a transparent margin of its own —
stretched to the portrait rect it clips every edge and the additive glow shows
through as a halo.

Not reproduced, deliberately: the banner's animated flame, and
`AncientBorderGlassOverlay` (a debug blur shader).

---

## The cards page

**Tiles are server-rendered, always.** `_card_tiles.html` is a partial holding
the tile loop, and `/cards?…&fragment=1` returns just that partial. `cards.js`
appends batches from it behind a "Load more" button. The point is that Jinja
stays the only thing that knows how to build a tile — the markup needs
`card_art`, `card_sprites`, `card_rules`, `card_text_len`, `changed_in` and
`card_runs`, and rebuilding that in JavaScript would fork the frame geometry,
keyword highlighting and rarity tints. A flag on the existing route rather than a
route of its own, because both need the same filter parameters and
`/cards/{card_id}` would shadow a sibling path.

Progressive enhancement is real here: the server always emits the pagination,
and `cards.js` hides it only once it has taken over, restoring it if a fetch
fails. `#card-grid`'s `data-*` attributes carry the state so the script never
parses the query string. Note `data-batch`, **not** `data-page` — see the traps.

`_CARDS_PER_PAGE = 36` is a multiple of 12 so the grid never ends on a ragged
row: `.card-grid` resolves to 4, 3 or 2 columns and 1 under the mobile
breakpoint, and 12 is their LCM. The "Showing X–Y" line must read the page size
from the route, not repeat the literal.

**Filter rows.** Every filter link is built by one `filter_url()` macro that
carries all dimensions through and clears only what is passed empty; writing
them by hand meant each row forgot a different one. Two lists feed the rows and
they are not the same: `card_characters` (the five plus **Colorless**) for the
character row, `characters` (the five) for the run-scope row — a card can be
Colorless, a run cannot. Curse, Status, Event, Token and Quest are reachable
from the rarity row instead, where "Other" groups Curse+Status+Quest via a
comma-separated `rarity` value.

## Card statistics

A tile shows two figures, **Held** and **Final**, and both come from the run
files. The cards page does not read `progress.save` at all.

**Run files** (`history/*.run`) — `players[0].deck` is the **final** deck;
`map_point_history[][].player_stats[]` carries `cards_gained`, `cards_removed`,
`cards_transformed` and `card_choices` (with `was_picked`). `_build_card_runs`
aggregates these into `card_runs`, keyed by card id and never truncated.

**`progress.save`** — the game's own lifetime counters (`times_picked`,
`times_skipped`, `times_won`, `times_lost`). Still parsed in `saves.py` and used
by the card *detail* page, but deliberately not by the cards list. Two reasons,
both worth knowing before anyone wires it back in:

- **`times_picked`/`times_skipped` cannot be made into a rate.** They exclude
  events entirely — `CARD.SQUASH` reads `times_picked: 0` while appearing in
  `cards_gained` ten times — and they treat shops asymmetrically: declining a
  shop card counts as a skip, buying one counts as neither. The quotient is
  reward picks over reward-offers-plus-shop-declines, which answers nothing.
  Rolling Boulder read `Picked 0/19 (0%)` from 21 shop offers, two of which it
  *bought*, for 247 and 523 gold.
- **`times_won`/`times_lost` mean "was in the final deck".** Verified: the
  progress totals equal our *final* count for 419 cards and our *held* count for
  none. Final is therefore the game's own metric, and **held is the only
  genuinely new number** — which is what "By Win Rate" sorts on.

Pick stats were shown for a while, recomputed from the run files with shop
floors skipped on both sides (25.2% overall against the 19.7% the raw counters
gave). They were removed as not useful. `_build_card_runs` still counts
`picked`/`offered` — nothing displays them, but they cost one loop and
`tests/test_card_runs.py` uses them to pin the offered-but-never-held case that
the "Played only" filter turns on.

Event offers are unrecoverable regardless: `event_choices` records only the
option taken, as a localisation key with a display string rather than a card id,
so the declined option — and any denominator — does not exist in the data.

### held vs final

- **held** — runs the card was in the deck at *any* point.
- **final** — runs it was still in the deck at the end.
- **kept** — count of the latter. Not displayed: it is exactly the denominator
  of the Final line, on all 442 cards, by construction.

They differ for 63 of 442 cards, and the bias runs one way: a card is most often
missing from the final deck *because you removed it*, so it is absent from
"final" precisely in the runs where you judged it worst. Spoils Map is the
clearest case — held 20 runs at 40%, final 7 runs at 0%.

`RunHistory.held` is evidence-only: the final deck plus every id appearing in
`cards_gained`, `cards_removed`, or either side of `cards_transformed`. Each
covers a case the others miss — a card removed at a shop is in neither the final
deck nor `cards_gained`, and a transform output is in none of the three.

**The starting deck is never recorded as a gain**, so `compute_analytics` seeds
it separately, deriving each character's decklist from `rarity == "Starter"`
rather than hardcoding. Without this, a starter cut before the end was invisible
— 24 runs ended with no Strikes at all. The invariant to check: every starter's
held count equals that character's run count, exactly.

Ascender's Bane needs no seeding: it is Eternal, so it is always in the final
deck, and the final deck is therefore a complete record of it.

### Acquisition channels — every card is attributable

Every unique card across 142 final decks, by how it arrived. There is no unknown
bucket: the five channels account for all of it.

| Channel | Share | Source |
| --- | --- | --- |
| Offered and picked | 56.5% | `card_choices.was_picked` |
| Starting deck | 19.2% | seeded from Starter rarity |
| Gained (event / shop / other) | 18.5% | `cards_gained` without a pick |
| Transform output | 3.3% | `cards_transformed.final_card` |
| Ascension curse | 2.4% | Ascender's Bane |

Picks are perfectly reliable: **every** picked card is also in `cards_gained`
(1520/1520), so `held` covers them without reading `card_choices`.

**Transforms are fully traceable — do not assume otherwise.** `cards_transformed`
records `original_card` and `final_card` together, 105 entries across 47 of 142
runs, so both sides of a Pandora's Box or transform event are recoverable:

```json
{"original_card": {"id": "CARD.STRIKE_SILENT", "floor_added_to_deck": 1},
 "final_card":    {"id": "CARD.PECK",          "floor_added_to_deck": 5}}
```

This field is easy to miss — Pandora's Box logs no gain and no removal, which
makes it look as though transforms vanish. They do not; they are recorded
separately. An earlier version of this document claimed a permanent ~3%
"no provenance" gap. That was wrong: it was a missing field, not missing data.

### Run scoping and the played filter

Two query params on `/cards` act on the statistics rather than the card list,
which is what distinguishes them from `character`, `type` and `rarity`:

- **`runs=<character>`** — recomputes every figure from that character's runs
  only. `compute_analytics` builds the record once overall and once per
  character from a shared `_build_card_runs`, so scoping is a lookup, not a
  recomputation. The invariant that makes it trustworthy: **for every card and
  every field, the per-character records sum exactly to the overall.**
- **`played=1`** — hides cards with no presence in the active scope.

- **`asc_min` / `asc_max`** — narrows the same figures to runs in an ascension
  range, and intersects with `runs` rather than overriding it.

`runs` is validated against the character list, **not** against which
characters have run history. Picking a character you have never played must
stay selected and show empty stats; validating against the data made it revert
silently to All runs, and the button did not even highlight. The ascension
bounds are fixed at 0–10 for the same reason: derived bounds would make a range
you have not played unselectable. The game caps at 10 and the highest here is
8, so the full range excludes nothing.

### The ascension range is summed, not recomputed

A *range* cannot be precomputed the way the six characters are — there are 66
of them. It is not recomputed per request either. `compute_analytics` builds
`card_runs_by_scope`, one record per `(character, ascension)` bucket, and
`sum_card_runs` adds up the buckets a range covers.

This works because every field `_build_card_runs` produces is a count of runs
and **every run falls in exactly one bucket**, so any scope is the sum of its
buckets — the same argument that makes the per-character split exact. Bucketing
costs one pass over the history regardless of how many buckets there are;
precomputing the ranges themselves would be 66 passes for the same answer.
`health_check.py` asserts the buckets sum to the unscoped record, which is what
would catch a run landing in two buckets or none.

**The full range is served from the precomputed record, not summed.** So the
default page costs exactly what it did before this filter existed, and — more
usefully — it returns the *same object*, so a bug in the summing path cannot
quietly change the numbers everyone sees by default.

`card_runs_by_scope` is nested `{character: {ascension: record}}` rather than
keyed by a `(character, ascension)` tuple. The whole analytics dict is
serialised by `/api/analytics`, and a tuple key survives `jsonable_encoder` as a
**list**, which cannot be a dict key at all — three tests fail with
`TypeError: unhashable type: 'list'` well away from the code that caused it.

**An inverted range is swapped, not honoured.** `asc_min=7&asc_max=2` is a
mis-click; taking it literally empties the page and reads as a broken filter.

### The headline win rate

The two figures above the grid are the sample every Held and Final number below
is drawn from. `run_counts_by_scope` is derived from the *same* buckets as
`card_runs_by_scope`, so the headline and the per-card figures cannot disagree
about which runs are in scope — counting the runs separately is exactly how
they would drift. Unscoped it equals `overview`'s total and wins by
construction, which `tests/test_card_runs.py` pins.

**It follows the run filters only.** `character`, `type` and `rarity` change
which cards are *listed*, not which runs are counted, so the number is
deliberately unmoved by three of the five filter rows. That is confusing enough
to be worth a caption rather than left to be inferred, and it is asserted: the
rate is identical across `?character=`, `?rarity=` and `?played=1`.

**Empty scope shows `—`, not `0%`.** A zero would claim every run in scope was
lost, which is a different and much stronger statement than there being none.
The denominator is always printed alongside for the same reason `_win_rate_key`
breaks ties on sample size: a rate without one is not interpretable, and this
codebase has already been bitten by one (`Picked 0/19 (0%)`).

The two bounds are a `<form>`, not links: as anchors the row would need one
link per endpoint and still could not express a range in one click. That has a
trap of its own — **a GET form replaces the query string wholesale**, discarding
even the action's own query, so every other active filter has to be restated as
a hidden input or it is silently dropped. `filter_hidden()` builds those from
the same `active_filters` dict `filter_url()` uses, because two hand-maintained
lists of the dimensions is exactly the drift `filter_url` was written to stop.

"Presence" for `played` is `held > 0`, not the existence of a record.
`_build_card_runs` also creates records for cards that were merely offered and
skipped — 75 of them — and keying off the record would let every one through
with empty Held and Final lines, which is exactly what the filter removes.


### Sorting

**A sort key must be a number the tile displays.** "By Win Rate" once keyed off
`analytics["card_rankings"]`, which `compute_analytics` truncates to the top 30
for the leaderboard — so all but 30 cards tied on a fallback and kept their
original order. The control looked wired up and moved almost nothing; the
response was a valid 200 throughout, so only a test that re-derives the key from
the rendered HTML catches it.

`card_rankings` is a leaderboard. **Never use it as a lookup table** — use
`card_runs`, which is untruncated.

**The list is always sorted.** There are three orders — Win Rate (the default),
Alphabetical and Cost — and no "however it came out of the file" option. There
used to be, and it was not a neutral order: `cards.json` is only *mostly*
alphabetical, with 24 inversions where the fetcher appended later batches, so
"Default" meant an order with no visible meaning that broke alphabetical in 24
unpredictable places.

**Every key ends in the card name**, which makes each order total: no two cards
can tie into an arbitrary position. Without that, ties fell back to file order
and inherited the same problem in miniature.

- **Win Rate** keys on `(-rate, -sample size, name)` — rate first, count only to
  break ties, so a 2/2 outranks an 8/19, deliberately. It follows the active run
  scope, so under `runs=Defect` it orders by the Defect numbers. Negated rather
  than sorted with `reverse=True`, which would flip the name tiebreak into
  descending too.
- **Cost** needs a rank, not a string compare: costs are strings and not all are
  numbers. A plain sort puts `12` before `2`, `?` before `0`, and `Unplayable`
  between `3` and `4`. `_COST_RANK` puts `?` and then `Unplayable` after every
  real cost.

An unrecognised `sort` falls back to the default rather than 400ing, so
`?sort=pickrate` — a live bookmark, since it went with the pick stats — still
renders a sensible page.

Beware that a sort can be a legitimate no-op. When every key ties, a stable sort
leaves the order untouched and the visible figures look unsorted, which reads as
a bug and is not one. With a fresh save and no run history that is exactly what
Win Rate does: every rate is 0, and the name tiebreak leaves it alphabetical.

### Testing this

`tests/test_card_runs.py` builds synthetic runs rather than reading the machine's
save, so the expected numbers are stated instead of derived.

**The HTTP fixture is not empty, and is not hermetic.** An earlier version of
this document claimed it had no run history. It does: `client` drives the real
app, and `config.SAVE_DIR` auto-detects `%APPDATA%\SlayTheSpire2\...`, which on
a machine that has played the game resolves to a full save. So an HTTP test that
does not patch `_get_runs` silently reads **the developer's own runs**.

That is why HTTP tests either assert invariants — relationships that hold at any
sample size — or **inject runs at the `sts2.app._get_runs` seam**. A count-based
assertion against the ambient save is the worst of both: it passes here for a
reason nobody can see and fails for anyone else.

Two tests were in exactly that state and are fixed:
`test_cards_winrate_sort_matches_displayed_rate` asserted the sort key
discriminates, which is only true once runs exist; and `test_cards_page_shows_
pick_rate` asserted the list shows a pick rate, a feature since removed — no
cards-page template renders "Picked", so it was passing only because its
fallback branch matched an unrelated `80%` in real data. It is now
`test_cards_page_ignores_progress_save`, pinning the actual rule.

The check that this stays true: **the suite must pass with `STS2_SAVE_DIR`
pointed at an empty directory.** It does, all 819 tests.

Injecting runs takes three patches, not one — the analytics cache is keyed by
ascension on a 60s TTL, so patching the accessor alone serves whatever a
previous test computed:

```python
with patch("sts2.app._get_runs", new=AsyncMock(return_value=runs)), \
     patch("sts2.app._analytics_cache", {}), \
     patch("sts2.app._analytics_cache_time", {}):
```

Both behaviours are mutation-checked: disabling the `played` filter and removing
the per-character split each make exactly one test fail. Worth preserving, since
the failure mode of this code is a valid-looking 200 with silently wrong ordering
or scope.

---

## Data quirks

**Suffixed card ids.** The wiki gives a card's event, quest and token
appearances their own pages, so the scrape lands ids the game does not have.
`DUAL_SUFFIXES` in `config.py` lists the suffixes, and they split two ways:

- **A base id also exists** (two Lantern Keys, two Spoils Maps). `knowledge.py`
  drops the suffixed one on load; `localize.py` resolves it back to the base.
  Filtered on load rather than edited out of `cards.json`, which the fetcher
  regenerates. Strike and Defend legitimately have five ids each, one per
  character — those are not duplicates.
- **No base id exists** — 13 of these, and they were *renamed* in `cards.json`
  (`CARD.CLASH_EVENT` -> `CARD.CLASH`). Saves reference only the base form, so
  the suffix meant every appearance in a run failed to match, and
  `_load_community_data` synthesised a "discovered" placeholder with no rarity,
  character or art. Six cards were split across two half-records that way. The
  rename is durable because `fetcher.py` matches by **name** before deriving an
  id, so a re-fetch reuses the renamed id rather than recreating the suffixed
  one. `patches.json` referenced one of them and was updated too.

**Rarity can be stale, and character is protected from correction.** Neow's
Fury, Relax and Brightest Flame are Ancient boons — granted by Neow, Pael and
Tezcatara at the start of an act — and were recorded as `rarity: Event`. The
wiki's Lua module has them right and `sources.py` reads it correctly, so this
was stale data, not a parse bug; a re-fetch fixes rarity on its own. Their
`character` would *not* have self-corrected: `_CURATED_CHARACTERS` in
`fetcher.py` protects Curse/Status/Token/Event/Quest so no source can overwrite
them, which is right for real event cards and wrong for these. Both fields were
corrected by hand and are now stable, since `Colorless` is not protected.

**One discovered placeholder remains:** `CARD.FOLLOW_THROUGH`, which appears in
a run but is in `cards.json` under no id at all — a different problem from the
suffix mismatch above.

**The wiki has two negative cost sentinels, and they mean opposite things.**
`Cost = -1` is a **variable (X) cost** — a real, payable card. `Cost = -2` is
**genuinely unplayable** — Curses, Statuses, Quest items. `_wiki_cost` maps
them; do not collapse them again.

Both were invisible for a long time because `_LUA_FIELD_RE`'s number branch was
`\d+`, which matches neither. The field was dropped from the parsed entry
entirely and the old `or "Unplayable"` fallback then made `-2` right *by
accident* and `-1` wrong — so Heavenly Drill, Whirlwind, Skewer, Volley,
Tempest, Cascade, Dirge, Eradicate, Malaise and Multi-Cast rendered with no
energy orb at all, indistinguishable from a Curse.

The trap on the way out is the mirror image: **fixing the regex without the
mapping swaps the bug over**, and every Curse comes through as a literal `-2`
drawn inside an energy orb. The in-memory re-fetch diff caught that — 31 cards
moving `Unplayable -> '-2'` — before anything was written.

Nothing else needed changing to render it. `cardart.py` keys the orb off
`cost.lower() == "unplayable"`, so `"X"` already yields an orb with X as its
text, and the energy sprites are per-character orbs with the number drawn
separately. `models.py` had documented `"X"` as a valid cost all along.

**19 cards have an empty `rarity`** and are stub records: 16 have no
description, 18 no art, all defaulting to `type: Skill`, `cost: Unplayable`,
`character: Colorless` — demonstrably wrong, since Star Blast uses the Regent's
stars and Training Strike references Cocoon. They are real to the game (18 of 19
have full text in the install's `localization/eng/cards.json`) but absent from
every public source, which is most likely *why* the fields are empty. Best
reading: implemented but not obtainable. Fixing them means reading the game's
own tables, which `localize.py` already knows how to do. **Do not add a filter
button for them** — it renders a page of blank tiles.

**41 cards have no art, from two unrelated causes.** Splitting on whether
`rarity` is filled in separates them: the ones with a rarity are version skew
(the install trails the card data), recoverable by upgrading and re-extracting —
except `Grapple`, which the wiki lists as deprecated. The other 18 are the stubs
above and upgrading will not touch them. **None of them appear in the run
history**, so every card actually played renders as a real card.

Editing `sts2/data/cards.json` by hand: it is serialised with
`json.dumps(data, indent=2)` — `ensure_ascii` left at its default `True` — plus
a trailing newline. Re-serialising with anything else rewrites all 8448 lines
and buries the real change. Assert the round-trip before writing:
`json.dumps(json.loads(raw), indent=2) + "\n" == raw`.

**`strategy.json` is hand-authored, and its odd formatting is deliberate — do
not reformat it.** It is the one data file matching no machine convention, which
makes it look like drift. It is not. Nothing writes it: the only reference in
the codebase is a read at `knowledge.py:178`, and it is not in `_save_json`'s
file list, so no update path touches it.

The layout is hybrid on purpose. The five character records are expanded at
indent 2, but each nested archetype object and each `general_tips` array is kept
on a **single dense line**, so one archetype reads as one line instead of eight.
Running it through `json.dumps(indent=2)` inflates it from 10,223 to 12,325
bytes and destroys that. The new `_no_data_writes` fixture in `tests/conftest.py`
will now catch anything that starts writing it.

---

## Known and unfixed

**`/analytics` overflows horizontally on narrow viewports.** At 460px the page
scrolls 235px wider than the viewport (`scrollWidth` 695); the offenders are a
479px-wide table and the `.bar-col` chart elements. **Pre-existing** — it
predates the card work and is slightly worse on `master` — so it is a known
limitation rather than a regression. Nothing else on the site does this.

**`CARD.FOLLOW_THROUGH`** appears in a run but is absent from `cards.json` under
any id, so it renders as a discovered placeholder. Unlike the suffix mismatches,
there is no record to rename.

**The 19 stub records** (see Data quirks) would need reading the game's own
tables rather than the wiki. `localize.py` already knows how.

---

## Environment traps

**Restart the server after any CSS or template edit.** Stylesheets are served
under `?v=<hash>` computed at startup, with `Cache-Control: max-age=3600`.
Editing CSS while the server runs changes nothing in the browser — not even a
hard reload.

**`data-page` is effectively reserved.** `[data-page]::before` is a global
decorative rule. Putting that attribute on a flex or grid container gives it a
pseudo-element that becomes a phantom child — it shifted every card one cell
along. Also note `.pagination { display: flex }` outranks the user agent's
`[hidden] { display: none }`, hence the explicit `.pagination[hidden]` rule.

**Do not round-trip files through PowerShell `Get-Content`/`Set-Content`.**
Windows PowerShell 5.1 reads as ANSI and writes UTF-8 *with BOM*, which mangles
every box-drawing character in `style.css`.

**Stop the server before `pip install`.** Windows holds
`.venv\Scripts\spirescope.exe` open, so the install half-fails and leaves the
package unimportable.

**Do not prefix shell commands with `cd`.** The working directory persists, and
`cd X; realcommand` defeats permission pattern matching. Use `git -C <path>` or
absolute paths.

**Screenshots need the Browser pane visible.** Otherwise the page does not
composite and `screenshot` times out. DOM reads, JS, network and console all
work regardless. `loading="lazy"` images never load in a non-compositing pane,
which looks exactly like broken images but is not.

**Upstream CI rejects AI attribution, and that is settled.**
`.github/workflows/no-ai-attribution.yml` fails on commit messages matching
`co-authored-by:\s*claude` or `noreply@anthropic.com`. It does not fire on forks
(Actions are disabled there by default). Commits here keep the trailer by
choice — see `CLAUDE.md` — so this fork does not contribute upstream while that
workflow stands. **Do not strip the trailers to make a contribution possible.**
Upstream also expects `pytest -q` and `ruff check sts2/ tests/` clean.

---

## Local files

`start-spirescope.cmd` and all of `.claude/` are untracked via
`.git/info/exclude`, keeping `git status` clean without touching the upstream
`.gitignore`. `.claude/launch.json` runs the `.cmd` on port 8000 so the Browser
pane can start the server; it needs the **absolute** path — a bare filename is
not found.
