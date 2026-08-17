# Working with the run data

Orientation for anyone — human or agent — doing statistics on this save history.
Read this before writing a query. Most of it is the accumulated result of
getting something wrong first.

## Where the data is, and which copy to use

There are **two** access paths, and they are not equivalent.

```python
from sts2 import saves
runs = saves.get_run_history()      # list[RunHistory], parsed and typed
```

That is the app's parser. It is convenient, typed, and **lossy**. It keeps what
the dashboard needs and drops the rest.

```python
import json, glob
from sts2.config import SAVE_DIR
for fn in sorted(glob.glob(str(SAVE_DIR / "history" / "*.run"))):
    d = json.load(open(fn, encoding="utf-8"))
```

That is the raw save. Every run is one JSON file. Floors live under
`map_point_history` as a list of acts, each a list of floor dicts:

```
{"map_point_type": "rest_site",
 "player_stats": [{...}],       # one entry per player; index 0 for solo runs
 "rooms": [{"room_type": ..., "turns_taken": ...}]}
```

**Check the raw file before concluding a field does not exist.** Real example:
`rest_site_choices` and `upgraded_cards` are recorded on every rest floor and
appear nowhere in `RunFloor` — the entire rest-site analysis was only possible
because someone looked at the raw JSON. Also raw-only: `gold_spent` /
`gold_gained` / `gold_lost`, `max_hp_gained` / `max_hp_lost`, and the
`cards_gained` / `cards_removed` / `cards_transformed` lists in their unmerged
form.

Use the parser for run-level work; drop to raw the moment you want a decision.

`extract.py` in this directory holds the builders the published analyses were
computed from — `raw_runs`, `elite_encounters`, `rest_decisions`,
`hazard_bands`, `balance`. They reproduce every figure in `ANALYSIS_LOG.md`
exactly. Start there rather than re-deriving the walk-forward and fatality
logic, both of which have subtleties.

**The encounter identity is `rooms[0]["model_id"]`** — not `encounter_id`, and
not on the floor dict. Guessing that one wrong yields 471 rows with blank names
and no error.

**Never join parsed and raw by list position.** `saves.get_run_history()`
returns runs **newest first**; `raw_runs()` returns them **oldest first**. They
are exact reverses, so `zip(parsed, raw_runs())` pairs the first run with the
last — for all 148 runs, and silently. Verified: joining by position gives
0/148 matching seeds; joining by id gives 148/148. Use `raw_by_id()`, keyed on
`RunHistory.id`, which is the `.run` filename stem and unique. `seed` works too.

**Floors are numbered by position.** `RunFloor.floor` equals the 1-based index
into the flattened `map_point_history`, verified on all 148 runs and re-checked
by `verify_assumptions()`. `hazard_bands` and `elite_encounters` both rely on
it, so if that check ever fails their floor numbers are wrong.

## Data hygiene, already checked

- **3 of 148 runs have `was_abandoned` set.** A quit is not a loss. Excluding
  them moves no published figure, but pass `skip_abandoned=True` for any new
  survival work.
- **One run has two players.** `player_stats` is a list; every extraction here
  takes index 0. Harmless at 1/148, wrong if multiplayer becomes common.
- **All 148 runs are `origin: vanilla`.** No modded saves to filter out yet.
- **`killed_by_encounter` and `killed_by_event` are populated on every run,
  including wins.** Their presence does not mean the run ended there. Derive
  death from "last recorded floor of a lost run" instead, as `_fatal` does.
- **968 `unknown` and 324 `ancient` floors have never been looked at.** That is
  a large slice of the map — roughly a quarter of all floors — entirely
  unexamined.

## The data model, and two things that will catch you

`RunHistory`: `id`, `character`, `win`, `ascension`, `seed`, `killed_by`,
`run_time`, `deck`, `held`, `relics`, `floors`, `enchantments`, and more.

`RunFloor`: `floor`, `type`, `encounter`, `monsters`, `turns`, `damage_taken`,
`hp_healed`, `current_hp`, `max_hp`, `gold`, `cards_offered`, `card_picked`,
`potions_used`, `potions_gained`.

**`held` already contains `deck`.** `saves.py` seeds the accumulator with
`held = set(deck)` and then adds gains, removals and both sides of every
transform. Verified: 0 of 148 runs have a card in `deck` that is not in `held`.
So `set(r.held) | set(r.deck)` is just `set(r.held)`. Starters are in `held`
too, for the same reason.

**`current_hp` on a rest floor is measured AFTER healing.** Verified on all 451
healing rests: `current_hp == previous_floor.current_hp + hp_healed`. To model
the *decision* you need the pre-choice value:

```python
pre_hp = (ps["current_hp"] - ps.get("hp_healed", 0)) / ps["max_hp"]
```

Getting this backwards inverts the thing you are trying to measure.

## Tooling

Use the MCP tools (`python_run`, `pytest`, `ruff`, `health_check`) rather than
shelling out — see `CLAUDE.md`. `python_run` already has the repo root as cwd
and `STS2_SAVE_DIR` set.

`numpy`, `scipy`, `pandas` and `statsmodels` are installed in `.venv` for
analysis. They are **not** in `pyproject.toml` and must not be added — the app
does not depend on them, and `build.py` builds the distributable from a separate
clean `.venv_build`, so the shipped binary is unaffected. Same status as Pillow.

**Do not hand-roll the statistics.** Two published numbers were wrong because of
this, in different ways:

- A hand-written two-proportion z-test returned p = 0.002 where Fisher's exact
  gives p = 0.0056 — anti-conservative on exactly the cell counts where the
  approximation was load-bearing.
- A hand-rolled gradient ascent reported a depth coefficient of +1.81. It had
  not converged and carried a stray ridge penalty; the MLE is **+3.21**, at a
  log-likelihood of −71.78 against the hand-rolled fit's −74.94. Nothing looked
  wrong — the estimate was stable, plausible, and reproducible.

Use `scipy.stats.fisher_exact`, `statsmodels.api.Logit`, and a
likelihood-ratio test for interactions. If you must roll your own, **compare
log-likelihoods against a reference implementation** — a converged fit is the
one with the higher likelihood, and that is the only cheap way to tell.

## Pitfalls

**1. Survivorship bias contaminates everything counted per run.** Winning runs
held 22.9 distinct cards; losing runs 17.8. So any card you must survive to
acquire is over-represented in wins before it does anything. Pooled by how often
a card was held, raw win rates run 42.3% / 36.6% / 37.0% / 28.4% against a base
rate of 28.8% — a pure artifact, and larger than any plausible card effect. The
same logic applies to relics, potions, floors reached, and anything else that
accumulates over a run.

**2. Run depth is a confounder *and* a mediator.** Surviving longer causes you
to hold more cards; holding good cards causes you to survive longer. Adjusting
for depth closes the back door and clamps part of the real effect; not adjusting
leaves the artifact. There is no unbiased choice — pick one, say which, and note
the direction of the residual bias.

**3. Pick your comparison group by checking balance, not by eye.** Comparing
elite lethality across acts measures act difficulty, not elite difficulty. This
error was made *twice* in one session: corrected once for act 1, then a
"late-act elites" group silently mixed a floor-28 pool with a floor-44 pool
(F(5,216) = 432.9 on floor). Before any between-group comparison, run an F-test
or ANOVA on the pre-treatment covariates — floor, ascension, arrival HP,
character mix. If they do not balance, the groups are not comparable.

**3b. Find draw pools by co-occurrence, not by position.** Act 1 has **two
disjoint elite pools**, one per act variant, and both occupy floors 7–15 — so
every floor-based grouping merges them without complaint. What separates them is
that two elites from the same pool appear in the same run and two from rival
pools never do (zero shared runs across the boundary, 12–38 within each).
`elite_cooccurrence()` computes this and `verify_assumptions()` guards
`ELITE_POOLS` against it. The same reasoning applies to any content the game
draws from alternative sets. Note only pools that are *alternatives* must be
disjoint — act-1 and act-2 elites co-occur constantly, because a run passes
through every act, and a first version of this check failed on 45 perfectly
legitimate pairs for exactly that reason.

**4. The unit of analysis decides whether you have any power.** Approximate
counts in the current history:

| unit | n |
|---|---|
| runs | 148 |
| boss fights | 261 |
| elite fights | 471 |
| last-rest-before-a-fight decisions | 581 |
| rest-site choices | 835 |
| ordinary monster fights | 1,303 |
| reward screens (shops excluded) | 2,227 |
| individual card offers | 6,970 |

Run-level questions are underpowered and will stay that way. Decision-level
questions start with 4–45× the sample **and** measure the outcome at the
decision, so there is no survivorship dilution to correct away. Prefer them.

**5. What the game randomises is where the causal leverage is.** Which card you
are offered, and which elite appears at an elite node, are drawn by the game.
What you take, and whether you walk into the node, are not. Between-elite
comparisons within one act pool are quasi-experimental — covariate balance
supports it (ascension F = 1.13, flat character mix). Exploit that; do not
apologise for it.

**6. But randomised does not mean powered.** The offer-as-instrument design is
clean and useless here: you take an offered card about 19% of the time, so an
intention-to-treat estimate is diluted to a fifth of the effect. A real +0.50
log-odds effect appears as +0.10 against a standard error of 0.45. Roughly 80×
the current run count would be needed.

**7. Near-deterministic behaviour destroys the counterfactual.** Rest-site
choice is almost a step function in HP — 92% heal below 35%, 2% heal above 80%.
At the extremes there is nothing to compare against. Only the middle of such a
distribution is learnable, and you must say so rather than quietly fitting the
whole range.

**8. Starters are in every run of their character by construction.**
`health_check.py` asserts it. Their win rate is exactly the character's win
rate and carries no information — exclude them from any card ranking.

**9. Shop card offers do not set `was_picked`,** so shop floors are excluded
from both sides of every pick statistic (8 of 2,063 set the flag). Event card
grants are unrecoverable entirely. Any "pick rate" is combat rewards only.

**10. Subgroup findings need the formal test.** Slicing HP into five bands
produced one significant result (p = 0.020 adjusted). The interaction term that
asks the same question without carving — does the effect vary with HP — came
back p = 0.205. Prefer the interaction; if you report the subgroup, report how
many you looked at.

**11. Normalise the prior when fitting a hyperparameter, and check the optimum
is interior.** Fitting τ by empirical Bayes with the Normal prior's *kernel*
— `exp(-β²/2τ²)` without the `1/(τ√2π)` factor — makes the marginal likelihood
rise monotonically in τ. It ran to the edge of the grid, reported "no
shrinkage", and produced a confident, plausible, entirely wrong card ranking
(top card +1.73 log-odds at 93% confidence). Adding the normaliser flipped the
answer to τ = 0. Nothing in the output looked wrong; the only tell was the
optimum sitting on the grid boundary. **Always report whether the fitted value
is interior to its search range.**

**12. Character and ascension confound almost everything.** Cards are
character-locked, so a global card ranking is largely a character ranking.
Ascension shifts the win rate materially. Condition on both, or restrict.

**13. The dataset grows under you.** The run count went 143 → 146 → 148 during
a single working session. Never cite a number from an earlier document without
recomputing it, and date anything you write down.

## A workable recipe

1. Pick the smallest unit that answers the question — decision over run.
2. Pull it from raw JSON if the parser does not carry the field.
3. Define the comparison group, then **test covariate balance** on it.
4. Fit with `statsmodels`; report odds ratios with confidence intervals, not
   bare p-values.
5. Ask whether the exposure is randomised by the game. If yes, say so — it is
   the strongest claim available. If no, name the confounder.
6. State what would change the answer, and what sample size it would need.
