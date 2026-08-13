# Working agreements

## Keep the co-authorship attribution

Commits made with Claude's help carry a `Co-Authored-By: Claude` trailer. **Keep
it. Always. Do not strip it, do not offer to strip it, and do not raise it as a
problem to be solved.**

This is a deliberate choice about honesty, not an oversight. The work was
co-authored; the commit history should say so.

### What that costs, and why it is fine

Upstream (`thequantumfalcon/spirescope`) runs
`.github/workflows/no-ai-attribution.yml`, which fails any commit message
matching `co-authored-by:\s*claude` or `noreply@anthropic.com`. So commits made
here **cannot be contributed upstream** while that workflow stands.

That is an accepted consequence, not a problem to route around. This fork is for
personal use. If upstream ever drops the prohibition, contributing back becomes
possible with no history rewriting — because the trailers were never removed.

The workflow does not fire on this fork; GitHub disables Actions on forks by
default, so nothing here is blocked day to day.

**Do not** rebase, amend, force-push or filter history to remove the trailers in
order to make a contribution possible. Removing them would misrepresent how the
work was done, which is the thing this agreement exists to prevent. If
contributing upstream ever genuinely matters, the honest routes are to ask
upstream to reconsider the policy, or to hand over the change as a description
for someone else to implement independently.

## Other conventions

- `DESIGN.md` — why the code is shaped the way it is.
- `OPERATIONS.md` — what to run, when, and what to check afterwards.
- `python scripts/health_check.py` after any data or art operation.
- `pytest -q` and `ruff check sts2/ tests/ scripts/` before committing.
