# Operations

What has to be run, when, and what to check afterwards. `DESIGN.md` explains
*why* things work the way they do; this is the *what to do*.

Everything here is local. Nothing is deployed, nothing is uploaded unless you
explicitly run a sync command, and no extracted game asset is ever committed.

---

## The one command that matters

```bash
python scripts/health_check.py
```

Read-only. Run it after any data or art operation below. It asserts the
invariants that have actually caught real breakage — id mismatches, per-character
statistics that do not sum, starter counts that disagree with run counts — and
exits non-zero if one trips. Lines marked `....` are informational counts that
legitimately change between game versions.

It needs `STS2_SAVE_DIR` set to reach the run-history checks; without it, it
skips them rather than failing.

---

## Routine: after the game updates

The single most common maintenance event. In order:

```bash
spirescope update          # refresh card/relic/potion data from the wiki
spirescope extract-art     # rebuild card art from the new install
spirescope localize        # rebuild translated card text (optional)
python scripts/health_check.py
```

Then **restart the server** — see the traps below.

What to look at afterwards:

| Signal | Meaning |
|---|---|
| "N of M cards have no art" fell | expected: the install has caught up with the card data |
| new `discovered placeholder` | a card in your runs that `cards.json` lacks, usually new content |
| `no suffixed id lacks its base form` FAILS | the wiki added an event/quest/token page; see below |
| `Cards (N)` on `/cards` jumped | check for duplicate names, which the health check also reports |

**`spirescope update` overwrites `sts2/data/*.json`.** Diff it before keeping —
the fetcher merges rather than replaces, but rarity and description churn is
normal and occasionally wrong. Two failure modes seen in practice: stale rarity
(the wiki corrects a card and the old value lingers until the next fetch), and
`character` never changing at all for Curse/Status/Token/Event/Quest cards,
which `_CURATED_CHARACTERS` deliberately protects from every source.

---

## Checking a card against the wiki's own data

When a card's rarity or colour looks wrong, do not read the rendered wiki page —
read the Lua module the data actually comes from, which is what `sources.py`
parses. It is authoritative and usually right when our copy is stale:

```python
import urllib.parse, urllib.request, json
title = "Module:Cards/StS2 data/Colorless"   # or /Ironclad, /Silent, /Defect, ...
url = ("https://slaythespire.wiki.gg/api.php?action=query&prop=revisions"
       "&rvprop=content&rvslots=main&format=json&titles=" + urllib.parse.quote(title))
req = urllib.request.Request(url, headers={"User-Agent": "spirescope-check/1.0"})
page = next(iter(json.load(urllib.request.urlopen(req))["query"]["pages"].values()))
print(page["revisions"][0]["slots"]["main"]["*"])   # grep for the card name
```

Each entry reads `Cost = 1, Color = "Colorless", Type = "Attack", Rarity = ...`.
`sources.WikiggSource().fetch_cards()` returns what our parser makes of it, so
comparing the two separates "the wiki is wrong" from "our parse is wrong" from
"our copy is stale". All three have happened.

---

## Routine: when the health check reports a suffixed id with no base

The wiki gives a card's event/quest/token appearance its own page, so the scrape
lands `CARD.X_EVENT` where the game and your saves only ever use `CARD.X`. Left
alone this splits the card in two: a real record with no run stats, and a
"discovered" placeholder with no rarity or art.

Fix by renaming the id in `cards.json` to the base form. It sticks across future
fetches because `fetcher.py` matches by name before deriving an id. Check
`sts2/data/patches.json` for references to the old id and update those too.

**Hand-editing `cards.json` — read this first.** It is serialised with
`json.dumps(data, indent=2)`, `ensure_ascii` at its default, plus a trailing
newline. Re-serialising with anything else rewrites all 8448 lines and buries
the real change in the diff. Always assert the round-trip before writing:

```python
assert json.dumps(json.loads(raw), indent=2) + "\n" == raw
```

The health check verifies this property, so a bad rewrite is caught even if the
diff is not reviewed.

---

## Routine: after changing card art code

Re-run extraction whenever you touch card ids, `UI_SPRITES`, `UI_TEXTURES`,
`_ART_VARIANTS`, or the tint tables:

```bash
spirescope extract-art
```

~6s. Writes to `%APPDATA%\SpireScope\cardart` — never into the repo. Stale
sprites from a previous run are **not** pruned, so if you remove an entry from
`UI_SPRITES` delete the orphaned PNGs by hand.

The app reads `manifest.json` once at startup, so a re-extraction has no effect
until the server restarts.

---

## Running it locally

```bash
start-spirescope.cmd
```

Sets `STS2_SAVE_DIR` and launches. `STS2_SAVE_DIR` must point at the **parent of
`history`** — `config.py` appends `history` itself, and aiming at `history`
fails silently with zero runs and no error.

First-time setup:

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[art]"     # the art extra is Pillow + texture2ddecoder
```

---

## Before committing

```bash
pytest -q
ruff check sts2/ tests/
python scripts/health_check.py
```

Add a `CHANGELOG.md` entry under `## Unreleased`.

Commits keep their `Co-Authored-By: Claude` trailer. Upstream's
`.github/workflows/no-ai-attribution.yml` rejects that, so this fork does not
contribute upstream while the workflow stands — an accepted trade, not a problem
to route around. **Do not strip the trailers.** See `CLAUDE.md`. The workflow
does not fire here, as GitHub disables Actions on forks by default.

---

## Traps that cost real time

**Restart the server after any CSS, template or art change.** Stylesheets are
served under `?v=<hash>` computed at startup with `Cache-Control: max-age=3600`,
and the art manifest is loaded once at startup. Editing CSS while the server
runs changes nothing in the browser — not even a hard reload. This is the single
most common way to conclude a fix "did not work".

**Stop the server before `pip install`.** Windows holds
`.venv\Scripts\spirescope.exe` open, so the install half-fails and leaves the
package unimportable.

**Do not round-trip files through PowerShell `Get-Content`/`Set-Content`.**
Windows PowerShell 5.1 reads as ANSI and writes UTF-8 *with BOM*, which mangles
every box-drawing character in `style.css`.

**Do not prefix shell commands with `cd`.** The working directory persists, and
`cd X; realcommand` defeats permission pattern matching. Use `git -C <path>` or
absolute paths.

**Verify UI changes by measuring, not by eye.** Several bugs this codebase has
had were invisible in a screenshot and obvious in the numbers: a sort that moved
almost nothing, a glow 26px proud at the top and 13px at the bottom, a grid
whose first cell was occupied by a pseudo-element. Read the DOM rectangles.

---

## What is *not* automated, and why

- **No scheduled data refresh.** `spirescope update` scrapes third-party sites;
  it runs when you choose to, not on a timer.
- **No art in CI.** Extraction reads the player's own game install, which CI
  does not have. `extract-art` is a local operation by design.
- **No automatic pruning of extracted art.** Removing a sprite from the tables
  leaves its PNG behind; see above.
- **Nothing extracted is committed.** The art is Mega Crit's. Extraction reads
  the user's install and writes to their state directory, mirroring what
  `localize` already does with card text. Keep it that way.
