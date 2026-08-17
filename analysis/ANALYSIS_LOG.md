# Analysis log

What has been tried, whether it was worth it, and what to do next.
Dated because the run history grows: everything below is as of **2026-08-17**,
at **148 runs / 42 wins**. Recompute before reusing a number.

Method notes and traps live in `DATA_GUIDE.md`; the dataset builders are in
`extract.py` and reproduce every figure below exactly. This file is the record
of results.

**Scale-setting result, true of everything here.** Character win rates span
17.4% (Defect, 8/46) to 42.9% (Silent, 9/21) — and that 25-point gap is **not
established**: χ² = 6.77 on 4 df, p = 0.15. At 148 runs even a difference that
large is consistent with luck. Any run-level effect smaller than "which
character you picked" is out of reach by construction.

**The one exception, and the headline of this file.** The act-1 variant is
assigned randomly by the seed, and it moves the win rate **+15.9 points**
(35.9% vs 20.0%, OR 2.39, p = 0.030). It is the only causal run-level result
here and the largest effect measured anywhere in the project — see section 5.
If you read nothing else, read that one.

---

## 1. Ranking cards by contribution to winning

**Question.** Can cards be ordered by how much they help, from how often each
was in the deck for a winning versus losing run?

**Approach.** Two stages. A run-level logistic baseline
(`character + ascension + log(cards held)`) to strip the confounders, then a
per-card log-odds shift with a `Normal(0, τ²)` prior, τ fitted by empirical
Bayes over a 1-D grid.

**Findings.**

- The naive statistic is **mostly a rarity ranking**. Pooled by how often a card
  was held, win rates run 42.3% / 36.6% / 37.0% / 28.4% against a 28.8% base —
  driven entirely by winning runs holding 22.9 distinct cards to losing runs'
  17.8. Correcting removes that gradient and slightly overshoots it:
  observed/expected of 0.89 / 0.95 / 0.99 / 1.05.
- The depth coefficient is **δ = +3.21** per log-card (se 0.76, p = 2×10⁻⁵);
  ascension **γ = −1.26** per ten levels.
- Within-character spread of card win rates is **at or below the binomial noise
  floor** for three of five characters.
- Empirical Bayes returns **τ = 0**. Every card's effect collapses to ±0.001
  log-odds. There is no distinguishable card signal in 148 runs.
- Resolving one card to ±0.25 log-odds needs ~61 runs holding it; the largest
  non-starter sample is 66, and it is Ascender's Bane.

**Useful?** Yes, as a negative result and a correction. It shows the app's
existing win-rate sort is measuring rarity, which is worth knowing. It does not
produce a defensible ranking. A ranking can be *manufactured* by fixing τ by
hand — that is an assertion the data does not support, and must be labelled as
such.

**What τ = 0.35 actually claims.** τ is the standard deviation of the per-card
effect on the **log-odds** scale, so under `Normal(0, τ²)` about two thirds of
cards fall within ±1 τ. At the model's 28.4% base that is **−6.6 to +7.6
percentage points** of win rate. The asymmetry is not a rounding artifact: a
fixed log-odds shift buys more probability upward than it costs downward at a
base below 50%. The write-up rounds this to "about ±8 points", which overstates
the downside — prefer the signed figures. τ = 0.6 would claim −10.5 / +13.6,
which is not defensible for a single card. Nothing about 0.35 is derived; it is
the smallest assertion that still produces a visible ordering.

**Correction, 2026-08-17.** The originally reported δ = +1.81 and γ = −0.62 were
**wrong**. They came from a hand-rolled gradient ascent that had not converged
and carried a ridge penalty: it reproduces +1.80 exactly, but at a
log-likelihood of −74.94 against the MLE's −71.78. The converged values are
above. This also moves the corrected observed/expected from a flat
1.03 / 1.05 / 1.04 / 0.99 to 0.89 / 0.95 / 0.99 / 1.05 — the rarity gradient is
still removed, but slightly over-corrected, which says the linear-in-log-depth
form is not quite right. Conclusions are unaffected: the naive statistic is
still an artifact and τ is still 0. `card_model.py` uses statsmodels and
reproduces the corrected figures.

**Trap hit along the way.** The first τ fit used the Normal prior's kernel
without its `1/(τ√2π)` normaliser, so the marginal likelihood rose monotonically
in τ, hit the grid boundary, and reported *no shrinkage* — yielding a confident
ranking with Feed at +1.73 log-odds and 93% confidence. Entirely an artifact.
See pitfall 11 in `DATA_GUIDE.md`.

**Written up.** `card-winrate-signal.md`, refreshed to 148 runs and carrying the
corrected coefficients. Also published as a private artifact:
<https://claude.ai/code/artifact/28f53109-f6ee-42a2-bb4d-e9619cb7ebc6>. Source
for the published version is in `artifact/`; rebuild with

```bash
{ printf '%s' "$(cat analysis/artifact/head.html)"; base64 -w0 sts2/static/fonts/kreon-latin.woff2; cat analysis/artifact/body.html; } > /tmp/card-winrate-signal.html
```

then republish that file, passing the URL above so it updates in place.

---

## 2. Offer-as-instrument (intention-to-treat)

**Question.** The offer is the one genuinely randomised event in a run. Can it
be used as an instrument to get a clean causal estimate?

**Findings.**

- Structurally it is the right design, and the apparent flaw is a feature: a
  screen shows three cards, so being offered X denies you Y — which makes the
  contrast "X against what you'd otherwise have been shown", exactly the
  decision-relevant quantity.
- There is ample raw material: 2,227 reward screens, 6,970 offers, 451 distinct
  cards, ~47 offers per run.
- **It is hopeless on power.** Median compliance is 19%, so ITT is diluted to a
  fifth of the effect: +0.50 log-odds shows up as +0.10 against SE ≈ 0.45.
  Signal-to-noise 0.22, needing roughly **80× the run count**.

**Useful?** Yes — cheaply closed off a direction that looks compelling on paper.
Worth revisiting only if the history grows by an order of magnitude.

---

## 3. Where runs die

**Question.** Where is the risk actually concentrated?

**Findings.**

- **The hazard is lumpy, not a ramp.** Conditional on reaching each band:
  1–5: 1.4% · 6–10: 6.2% · 11–15: 5.8% · 16–20: 12.4% · **21–25: 23.0%** ·
  26–30: 10.3% · **31–35: 20.5%** · 36–40: 3.2% · 41–45: 3.3% · **46–50: 27.6%**.
  Three walls with a near-free corridor between the last two.
- By what you are facing: **boss 16.1% fatal** (42/261), **elite 7.9%**
  (37/471), **ordinary monster 1.8%** (24/1303). Elites are fought nearly twice
  as often as bosses, so they kill almost as many runs in absolute terms.
- **Decimillipede is genuinely harder than its pool-mates**: 22.6% (12/53) vs
  9.0% (9/100) for Entomancer and Infested Prisms, p = 0.020 — and the
  comparison is quasi-randomised, since covariate balance within the pool holds
  (floor F = 0.13, ascension F = 1.13, flat character mix).
- **Arrival HP dominates enemy identity about 7:1.** Within that pool, share of
  fatality variance explained: which elite it is **0.039**, HP band **0.268**.
  Below 50% HP an act-2 elite is fatal 47.1% of the time against 4.2% above —
  RR 11.2, p = 1.5 × 10⁻¹⁰. Across all elites, 32.0% vs 3.3%.

**Corrections made along the way.** An initial "Decimillipede is a *finisher*"
interaction — much deadlier specifically when you arrive hurt — **did not
survive** proper pool matching. It was an artifact of comparing against act-1
elites (easy) and act-3 elites (met at high HP). Against its real pool-mates the
low-HP gap is 64% vs 35%, p = 0.09.

**Useful?** **The most productive analysis so far.** Large effects, real
significance, and an actionable rule: do not fight an act-2 elite below half
health, and if you must, Decimillipede is the worst one to draw.

**Caveat.** The HP effect is not clean — arriving at 30% is a decision and also
a marker of a run already going badly. Same confounder/mediator bind as run
depth. The between-elite comparison *is* clean; the HP comparison is not.

---

## 4. Rest-site choice before an elite or boss

**Question.** At the last rest site before an elite or boss, is there signal in
heal-versus-forge as a function of HP — and is the choice right?

**Data.** 835 rest sites carry `rest_site_choices` (raw JSON only): 403 forge,
376 heal, 42 doing both via the tent relic. 581 are the last rest before a
fight; 543 are a clean either/or.

**Findings.**

- **The choice is near-deterministic in HP.** Share choosing heal: 92.2% below
  35%, 81.9% at 35–50%, 58.1% at 50–65%, 22.0% at 65–80%, 2.0% above 80%.
  Crossover around 60–65%.
- **Whether the choice is *right* is not established.** Logistic over all 543
  decisions: pre-choice HP **OR 0.03** (p < 0.001), facing a boss **OR 3.30**
  (p < 0.001), choosing forge **OR 1.61, 95% CI 0.72–3.61, p = 0.249**.
- The 35–50% band looks bad for forging — OR ≈ 5.5, p = 0.020 after adjusting
  for an ascension imbalance (5.74 vs 3.38) that did *not* explain it away. But
  it is one of five bands examined, and the formal interaction test
  (forge × HP) returns **p = 0.205**.

**Useful?** Half. The descriptive result is solid and interesting — the decision
rule is essentially a fixed HP threshold. The causal question is underpowered:
the confidence interval spans "mildly protective" to "3.6× worse".

---

## Cross-cutting lesson

Every run-level question has come back underpowered, and every decision-level
question has produced something. 148 runs is a small sample; 2,227 reward
screens and 835 rest choices are not. **Choose the unit first.**

The second lesson is that the comparison group is where the errors live. Three
separate findings in this log were overturned or downgraded by fixing the
control group, not by collecting more data.

---

## 5. The act-1 variant

**Question.** Act 1 comes in two variants — Overgrowth and Underdocks — and each
draws elites from its own disjoint pool of three. Does that matter?

**How the pools were found.** Not by floor: both sit at floors 7–15, so every
floor-based grouping merges them silently. **Co-occurrence separates them** —
two elites in the same pool turn up in the same run, elites in rival pools never
do. Zero shared runs across the boundary, against 12–38 within each.
`elite_cooccurrence()` does this, and `verify_assumptions()` now guards it.

| pool | elites | act-1 elite deaths |
|---|---|---|
| Overgrowth | Bygone Effigy, Byrdonis, Phrog Parasite | 9/97 = **9.3%** |
| Underdocks | Phantasmal Gardeners, Skulking Colony, Terror Eel | 5/152 = **3.3%** |

**Findings.**

- **The variant predicts the run.** Underdocks wins 28/78 = 35.9%, Overgrowth
  14/70 = 20.0%. Fisher p = 0.044. Adjusted for ascension and character the
  effect *strengthens*: **OR 2.39 (95% CI 1.09–5.24), p = 0.030**, LR test
  p = 0.026.
- **It is not your own improvement in disguise.** The obvious threat was that
  one variant clustered in a period when you played better. It does not:
  Underdocks share by quarter of the history runs 54% / 60% / 46% / 51%, and
  Mann-Whitney on position gives p = 0.486. Ascension (3.67 vs 3.89) and
  character mix are close.
- **There is a mechanism.** Overgrowth's elites are about three times deadlier
  per fight (9.3% vs 3.3%, Fisher p = 0.053), and Overgrowth runs fight fewer
  act-1 elites: **1.39 vs 1.95** per run.
- **The elite-count gap is not survivorship.** The obvious objection is that
  Overgrowth runs simply die before getting the chance. They do not: among runs
  that **completed act 1**, the gap is **2.01 vs 1.42** (Mann-Whitney
  p = 0.0006), essentially identical to the unconditioned +0.56 and if anything
  slightly wider. So it is either structural — fewer elite nodes on those maps —
  or behavioural, routing around elites known to be dangerous. Distinguishing
  the two needs map topology (next step 5).
- **Most of the advantage is realised in act 1.** Overgrowth loses **35.7%** of
  runs before the act-1 boss against Underdocks' **11.5%** (Fisher p = 0.001) —
  a 24-point survival gap, larger than the 15.9-point gap in final win rate.
  Among runs that clear act 1 the remaining difference is +9.5 points and no
  longer resolvable (40.6% vs 31.1%, p = 0.328).

  Treat that decomposition as descriptive only. "Completed act 1" is a
  post-treatment variable, so conditioning on it forfeits the randomisation and
  can induce collider bias; and the surviving sample is smaller, so the loss of
  significance is partly lost power rather than a vanished effect. It locates
  where the effect happens; it does not measure a residual.

**A tempting association to not act on.** Among act-1 survivors, act-1 elites
fought tracks win rate hard: 0 elites → 0/11 won, 1 → 35.5%, 2+ → 43.1%. This is
almost certainly confounded in the usual direction — you route into elites when
your deck is already strong, so the arrow plausibly runs backwards. It is the
same shape as the card rarity artifact in section 1. Do not read it as "fight
more elites".

**The variant is assigned randomly by the seed** (confirmed by Andrew, 2026-08-17).
That changes the status of this result completely. It is not an association that
survived adjustment — it is a **randomised experiment the game runs for you**,
and the observed covariate balance is a consequence of the randomisation rather
than a lucky break. The effect is causal.

In the units a player cares about: **+15.9 percentage points of win rate**,
95% CI **+1.7 to +30.1**. Wide, but it excludes zero, and the lower end is still
larger than any card effect the data can resolve.

**Do NOT adjust this for run depth.** Adding `log_depth` drops it to OR 1.99,
p = 0.114, which looks like the finding evaporating and is actually the analysis
breaking. Depth is *downstream* of the variant — Overgrowth's deadlier elites end
runs sooner and get fought less often, so depth is precisely the channel the
effect travels down. Conditioning a randomised treatment on a post-treatment
variable is textbook over-adjustment. Character and ascension are fine to
include: both are settled before the seed draws the act.

For the same reason, **do not add the variant to `baseline()`**. It gains little
there (LR p = 0.114) exactly because `log_depth` already absorbs it, and the
change would invalidate the reproduction checks for no real gain.

**Useful?** This is **the only causal run-level result in the project**, and the
largest effect measured anywhere in it. Character never cleared the noise bar
(p = 0.15); cards returned τ = 0. The one thing that demonstrably moves this
player's win rate is a coin the game flips before the run starts.

**Remaining caveat.** p = 0.030 on a single test, against a lot of testing across
this history. It was looked at for a structural reason rather than found by
fishing, and it now has randomisation and a mechanism behind it — but it wants
replication as the history grows.

---

## Blocked: why the elite-avoidance question cannot be answered

Overgrowth runs fight 1.42 act-1 elites to Underdocks' 2.01, and that gap is not
survivorship (section 5). So it is one of two things, with opposite
implications:

- **Structural** — fewer elite nodes on Overgrowth maps. The variant is simply
  poorer and there is nothing to change.
- **Behavioural** — routing around elites known to be dangerous. The avoidance
  may itself be the mistake, since skipping elites forfeits their rewards.

Separating them needs to know what the map *offered*, not just what was walked.
Both available sources have been checked and neither has it.

**The saves record only the walked path.** Across all 4,796 floors,
`map_point_history` floor dicts have exactly three keys — `map_point_type`,
`player_stats`, `rooms`. No connectivity, no coordinates, no visited flag, and
every floor carries `player_stats`, which an unvisited node could not. Act 1
reads as a linear 17-step walk from Neow to boss. It is a history, not a map.

**The game archive does not have the generator either.** Map generation is
entirely C#, and every relevant file is stripped to 1 byte in the `.pck`:
`Map/StandardActMap.cs`, `Map/MapPointTypeCounts.cs` — precisely the one that
would define how many elite nodes an act gets — `Map/MapPostProcessing.cs`,
`Map/MapPathPruning.cs`, `Map/GoldenPathActMap.cs`. No `.tres` carries the
parameters; the only non-code act data in the archive is five localisation
titles.

**Two routes remain, neither of them analysis.** Ask the player, who knows
whether they deliberately avoid Overgrowth elites — twice already in this
project a piece of game knowledge has settled something the data could not.
Or decompile the shipped .NET assembly, which is a different kind of
undertaking and out of scope here.

Until then the mechanism behind the largest effect in this project is
**undetermined**, and the log should not imply otherwise.

---

## Judgement calls, flagged as such

Everything above this line is a measurement. These are not — they are opinions
formed while doing the work, and a new reader should feel free to overturn them
without needing new data.

- **The next-steps ordering is a guess.** Particularly potions at #3. That rests
  on a hunch that unused potions at death is a common player error and therefore
  a large effect. Nothing here measured it; `potions_used` has never been
  touched. If it turns out flat, the ranking was wrong, not the data.
- **τ = 0.35 was chosen, not derived** (see above). A different analyst could
  justify 0.2 or 0.5 as easily.
- **Controlling for depth rather than leaving it out** is a choice between two
  biases, taken deliberately toward the conservative one. Someone arguing the
  other way — that the artifact is smaller than the clamped signal — would not
  be obviously wrong, and would get a different card ranking.
- **`.venv` now carries numpy, scipy, pandas and statsmodels (~100 MB)** for
  analysis the shipped app never runs. Deliberate, and isolated from the
  distributable, but it is a cost someone else might weigh differently.
- **Excluding starters from card rankings** is right for the ranking question
  and wrong for others: they are the only cards with full-sample coverage, so
  anything about *deck composition* rather than card choice should keep them.

---

## Suggested next steps

Roughly in order of expected value.

1. **Decompose the floor 21–25 wall.** The largest single block of deaths (26)
   and currently unexplained — spread across Decimillipede, Entomancer and
   several ordinary monsters. Worth knowing whether it is an act transition, a
   difficulty step, or a deck-strength plateau.
2. **Boss fights.** 42 deaths, 261 encounters, and the `boss` term already
   carries OR 3.30. Apply the elite treatment: pool by act, check balance, then
   ask whether arrival HP dominates identity there too.
3. **Potion usage.** `potions_used` and `potions_gained` are on every floor and
   nothing has touched them. Unused potions at death would be a clean,
   decision-level, high-n question — and a classic player error, so there is a
   real chance of a large effect.
4. **Fix the depth adjustment's functional form.** `baseline()` puts depth in as
   a straight line in `log(cards held)`, and it over-corrects: observed/expected
   runs 0.89 / 0.95 / 0.99 / 1.05 across the held-count buckets when a correct
   adjustment would be flat at 1.00. That table *is* the goodness-of-fit test —
   it needs no extra machinery, just re-running it after each attempt. Try a
   quadratic in log-depth, a spline, or binning depth into quantiles.

   This ranks higher than it looks. `baseline()` is the shared run-level
   correction, so anything built on it inherits the mis-specification — and it
   is the adjustment standing between a raw statistic and the survivorship
   artifact that motivated this whole exercise. Fixing it will not change τ = 0,
   which is the point: it is cheap insurance for every future analysis rather
   than a way to rescue this one.
5. **Path choice: taking versus skipping elites — BLOCKED, see below.** The
   question is live and interesting; the data to answer it does not exist.
6. **Fix the app's card sort.** Independent of any new analysis: the current
   Win Rate order is a rarity ranking. `_sort_key_winrate` in `sts2/routes.py`
   keys off raw held win rate with sample size only as a tiebreak — a 2/2
   outranks an 8/19 by design. Either adjust for run depth or relabel it.
7. **The 968 `unknown` and 324 `ancient` floors.** About a quarter of all
   floors, never examined. `unknown` is presumably events; worth at least
   identifying before assuming they are inert.
8. **Re-run the rest-site analysis (section 4 above) when the history has grown
   materially** — the 35–50% forge result is the one live lead that more data
   would settle.
