# Does a card's win rate tell you anything?

A look at whether you can rank Slay the Spire 2 cards by how much they help,
using one player's run history. It goes three ways, and the third is the
interesting one:

1. The obvious statistic is measuring something else entirely — mostly rarity.
2. Fix that, and 146 runs contain no detectable card signal at all.
3. The cleaner design you'd reach for next is *worse*, not better. Which is a
   hint that the question was the wrong shape to begin with.

The data is 146 completed runs, 42 of them wins (28.8%), read out of the game's
own save files. Per character:

| character | wins / runs | win rate |
|---|---|---|
| Defect | 8 / 46 | 17.4% |
| Necrobinder | 9 / 31 | 29.0% |
| Ironclad | 8 / 29 | 27.6% |
| Silent | 9 / 21 | 42.9% |
| Regent | 8 / 19 | 42.1% |

Worth pausing on that table, because it sets the scale for everything below.
It looks like a 25-point spread between best and worst character. It isn't
established: χ² = 6.61 on 4 degrees of freedom, p = 0.16. At this sample size
even a gap that large is consistent with luck. Hold that thought.

For each card we know how many runs held it at any point, and how many of those
were won. The obvious statistic is *of the runs that held this card, what
fraction were wins*. That's what most trackers show.

## The obvious statistic is measuring rarity

Group cards by how many runs held them, and pool the win rates:

| runs holding the card | pooled win rate |
|---|---|
| 1–2 | 42.3% |
| 3–5 | 36.6% |
| 6–10 | 37.0% |
| 11+ | 28.4% |
| **base rate** | **28.8%** |

Cards you've barely seen win 42% of the time. Cards you see constantly win 28%.
That is not a fact about card quality. It's a fact about run length:

> Winning runs held **22.9** distinct cards on average. Losing runs held **17.8**.

If you die on floor 6 you saw a dozen cards. If you win you saw thirty-odd. So
*any* card you have to survive to acquire is over-represented in winning runs,
before it does a single point of damage. The rarer the card, and the later you
tend to get it, the bigger the free bonus.

This is survivorship bias in its purest form, and it's large — a 14-point spread,
far bigger than any plausible effect a single card has on whether you win. A card
list sorted by raw win rate is, to a first approximation, sorted by rarity.

It also means the usual defences don't help. Requiring a minimum sample size
doesn't remove the bias, it just removes the cards where it's worst, and takes
every rare card with them.

## Correcting it

The fix is to stop comparing a card's runs against *all* runs, and start
comparing them against runs that looked alike. Fit a model of the run, not the
card:

```
logit P(win) = intercept + character
             + γ · (ascension / 10)
             + δ · log(cards held)
```

Fitted on the 146 runs, this gives **δ = +1.81** per log-card and
**γ = −0.62** per ten ascension levels. The deck-size term is doing enormous
work, which is another way of saying the bias above is real and big.

Now each run has a predicted win probability that already accounts for who was
playing, how hard, and how far they got. A card's runs can be judged against
*that* instead of against the global average. Re-running the same buckets, now
as observed wins over expected wins:

| runs holding the card | observed / expected |
|---|---|
| 1–2 | 1.03 |
| 3–5 | 1.05 |
| 6–10 | 1.04 |
| 11+ | 0.99 |

Flat. The rarity gradient is gone.

## The bind

That correction is not as clean as it looks, and the reason matters for
everything after it.

Run depth is **both a confounder and a mediator** of the same relationship.
Surviving longer causes you to hold more cards — that's the back door, and it's
the artifact above. But holding good cards causes you to survive longer — that's
the causal path you're trying to measure. Same variable, pointing both ways.

So there is no adjustment that is simply correct. Control for depth and you
close the back door *and* clamp part of the real effect. Don't, and the artifact
swamps everything. You are picking which direction to be wrong in. Everything
below uses the controlled version, which biases toward finding nothing.

## What's left underneath

With the artifact removed, do cards differ measurably?

The first check doesn't need a model. Within a single character, card win rates
will vary *even if every card is identical*, purely because each is measured on a
handful of runs. That expected wobble can be computed. Compare it to the wobble
actually observed:

| character | cards | observed sd | noise sd | excess |
|---|---|---|---|---|
| Regent | 22 | 0.109 | 0.189 | 0.000 |
| Defect | 58 | 0.124 | 0.133 | 0.000 |
| Necrobinder | 39 | 0.192 | 0.171 | 0.089 |
| Ironclad | 30 | 0.181 | 0.168 | 0.066 |
| Silent | 21 | 0.098 | 0.176 | 0.000 |

Three of five characters show *less* spread than pure noise predicts. Two show a
trace more, of a size you'd expect from estimating a variance on thirty-odd noisy
points.

The proper version is a Bayesian model with two stages. Stage one is the
run-level baseline above. Stage two gives each card its own log-odds shift on top
of its runs' predicted probabilities:

```
logit P(win) = (predicted for this run) + βᵢ        βᵢ ~ Normal(0, τ²)
```

τ is how much cards genuinely differ. Rather than assume it, you can estimate it
from the data — that's empirical Bayes, and it's the honest move, because it lets
the data say *not much*.

**It says τ = 0.** Every card's estimated effect collapses to ±0.001 log-odds.
The chance any given card "helps" lands between 28% and 32%, where 50% means no
evidence either way. The model is not broken; it is reporting that after removing
the survivorship artifact, 146 runs contain no distinguishable card signal.

For scale: pinning one card's effect to ±0.25 log-odds needs about **61 runs that
held that card**, and +0.5 log-odds is already a *large* effect — the difference
between a 31% and a 43% win rate, from one card. The largest non-starter sample
here is 66 runs, and the card is Ascender's Bane, the curse you get for playing
at high ascension.

## What were we actually asking?

At this point it's worth being precise about the question, because three
different ones have been quietly sharing a name:

1. *Which cards show up in my winning runs?* Descriptive. Needs no model.
2. *Does having this card raise my win probability?* A marginal causal claim.
3. *At a reward screen, which of these three should I take?* The actual decision.

Everything above answers (1) with the confounders scrubbed off. It bears on (2)
only weakly, and on (3) barely at all — because (3) is a **contrast against the
alternatives**, not a property of the card. "Feed is worth +0.3" isn't well posed
until you say *instead of what*. A card can raise your win rate and still be the
wrong take, because the card beside it is better.

That also explains why "ever in the deck" is such a weak signal. It mixes your
choices, the game's impositions (curses, event grants, transforms) and how long
you survived, and it has no counterfactual: runs with the card differ from runs
without it in every other way too — most of all in how far they got.

## The one thing the game randomises

Here's the lever. In a run, *what you are offered is random; what you take is
not.* The offer sits upstream of both your judgment and your depth, so it breaks
the bind.

And the obvious objection turns out to be the point. Being offered Feed isn't a
clean instrument, because a screen shows three cards — being offered Feed
*denies* you something else, so the offer moves the run through more than Feed
alone. But that displacement is exactly the "instead of what" that question (3)
needs. Comparing runs where a card was offered against runs where it wasn't
estimates it against the distribution of what you'd otherwise have been shown.
The flaw is the estimand.

There's plenty of it, too: 2,206 usable reward screens across the 146 runs,
6,893 card offers, 451 distinct cards — about 47 offers per run.

## Clean, and worse

So the clean design should win. It doesn't:

| | bias | typical standard error | signal |
|---|---|---|---|
| held, depth-adjusted | unknown, plausibly ±0.5 | 0.45–0.75 | undiluted |
| offered vs not offered | ~none | 0.41–0.44 | diluted to **19%** |

The killer is compliance. You take a card in about one screen in five where it
appears, so an offer-based estimate is diluted to roughly a fifth of the effect
you care about. A real +0.50 log-odds effect of *taking* a card shows up as
+0.10, against a standard error of about 0.45 — a signal-to-noise ratio of 0.22.
Getting that to something usable is on the order of **80× the current run
count**.

Meanwhile the biased estimator has comparable standard errors and no dilution,
but a bias about as large as the effect it's hunting. The clean design is too
weak; the strong design is too dirty. They fail in different ways at roughly the
same place, which is a good sign that the sample, not the method, is the binding
constraint.

## The scalar is the wrong shape anyway

One number per card assumes a card has one value. It doesn't. Feed is strong in
a deck that can already kill and dead weight in one that can't. The honest
quantity is an interaction between the card and the deck it lands in — and 146
runs cannot support main effects, let alone interactions.

## So can you rank cards at all?

Yes, but you have to be explicit that you're asserting something the data doesn't
prove. τ = 0 means *these runs can't resolve the differences*, not *the cards are
equal* — and we know from the game's design that they aren't.

So fix τ by hand instead of fitting it. Setting τ = 0.35 — a claim that most
cards land within about ±8 points of win rate — gives a bounded, data-driven
ordering where nothing overstates itself:

```
Feed             +0.32   chance it helps: 83%   (8 runs,  Rare)
Time's Up        +0.29                    81%   (8 runs,  Rare)
Infinite Blades  +0.28                    81%   (11 runs, Uncommon)
...
Leap             −0.26                    16%   (30 runs, Common)
TURBO            −0.32                    15%   (11 runs, Common)
```

Every number is a shrunk estimate; the ordering is a weak prior, not a verdict.
Turn τ up and the spread widens — that dial is exactly *how much am I willing to
claim*. It's useful for breaking a tie you're already indifferent about, and
useless as evidence that Feed is good.

## Caveats worth knowing

- **This is one player's history.** It measures how these cards performed in
  *these* hands, at this ascension mix, with this deck-building. It is not a
  claim about card strength in general.
- **The depth adjustment biases toward nothing.** Per the bind above, it clamps
  part of the real effect along with the artifact.
- **`kept` is not a stronger signal than `held`, it's a worse one.** Whether a
  card survived to the end of the run is a *post-treatment* outcome — cards get
  cut because of how the run was going. Good for describing winning decks,
  treacherous for anything causal.
- **Cards are character-locked.** A global ranking is largely a ranking of
  characters. Comparisons only mean much within one.

## The takeaway

Two things, one negative and one useful.

The negative one is the correction. Any card win rate you see in a tracker —
including trackers with far more runs than this — is inflated for rare and late
cards unless it explicitly adjusts for how far the run got. If the rares look
strong in such a list, check whether you're reading card quality or reading
survivorship.

The useful one is about sample size, and it's more humbling than it first looks.
Card identity explains nothing detectable here. But neither does character: that
25-point spread in the opening table doesn't clear the noise bar either. At 146
runs the only thing that clearly separates winning from losing is how far the run
got — which is very nearly a restatement of winning.

That suggests the run is the wrong unit of analysis. There are 146 of them, but
there are 2,206 reward screens and 6,893 card offers underneath. Questions posed
at the level of the decision rather than the run start with fifteen to forty
times the sample: where runs actually die, what the deck looked like going into
the fight that ended it, whether a choice predicts surviving the next five floors
rather than the whole run. That's where 146 runs still has something to say.

---

*Numbers produced with [Spirescope](https://github.com/thequantumfalcon/spirescope),
a local-first StS2 run tracker that reads the game's own save files. The analysis
above is on top of its run history, not a feature of it.*
