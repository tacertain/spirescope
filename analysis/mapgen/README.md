# Regenerating the offered map

The saves record only the path walked, never what the map offered
(`DATA_GUIDE.md` pitfall 3d). This directory reconstructs the missing half by
replaying the seed through the real game engine.

Everything here was built and validated on **game v0.107.1** on 2026-08-17.

## What is in here

| file | what it is |
|---|---|
| `sts2-cli-v0.107.1.patch` | the four fixes needed to make sts2-cli build and run against v0.107.1 |
| `ApiDump/` | tiny Mono.Cecil tool that prints the shape of types in `sts2.dll` — the fastest way to find the next API drift |
| `genmaps.sh` | batch driver: a TSV of runs in, one JSON map per line out |
| `reconstruct.py` | recovers the walked column, and with it offered-versus-taken data |
| `maps-v0.107.1.jsonl` | the 89 regenerated act-1 maps, so the 20-minute batch need not be rerun |

## Setting it up from scratch

1. **.NET 9 SDK.** `winget install Microsoft.DotNet.SDK.9`. Runtimes are not
   enough. `dotnet` lives at `/c/Program Files/dotnet` and may not be on PATH.
2. **Clone** <https://github.com/wuhao21/sts2-cli> (this was at
   `C:/Users/tacer/GitHub/sts2-cli`; override with `STS2_CLI`).
3. **Apply the patch:** `git apply /path/to/sts2-cli-v0.107.1.patch`.
4. **Run setup with an explicit game path** — its Windows branch points at the
   install root, but the DLLs are one level down. Its macOS branch points into
   the equivalent subdirectory, so this looks like an oversight:

   ```bash
   bash setup.sh "C:/Program Files (x86)/Steam/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64"
   ```

5. **Build:** `dotnet build src/Sts2Headless/Sts2Headless.csproj`.

The setup script only ever *copies* DLLs out of the Steam install and patches the
copies in `lib/`. It does not modify the game.

## What the patch contains

Three code changes across two files. **Two are version drift** and should be
expected to recur on the next game update:

1. `SetUpSavedSinglePlayer` → `SetUpSavedSingleplayer`. A capitalisation rename.
   Only on the save-loading path, not `start_run`.
2. `RunState.CreateForTest` now reaches `ModelDb.BadgeModels`, which calls
   `ReflectionHelper.ModTypes` and throws while `ModManager.State` is `None`.
   `ResetForTests()` clears the mod list but leaves the state at `None`, so the
   private setter is driven to `Skipped` — "finished, with no mods", which is the
   truth headless.

**The third is a feature, not a fix**, and will not be obsoleted by a game
update:

3. An `act1` argument on `start_run`, spanning both files. The default act list
   is hardcoded to Overgrowth, so without this the act-1 variant cannot be
   selected and the two variants cannot be compared at all.

Steps 1 and 4 of the setup above — the SDK install and the explicit game path —
are environment work and are deliberately *not* in the patch.

When the game updates and something else breaks, use `ApiDump` rather than
guessing:

```bash
dotnet run --project tools/ApiDump -- lib/sts2.dll type RunManager
dotnet run --project tools/ApiDump -- lib/sts2.dll find SetUpSaved
```

## Upstream status — read before applying the patch

As of 2026-08-17 the **two compatibility fixes have been submitted upstream** as
a draft PR: <https://github.com/wuhao21/sts2-cli/pull/90>, from branch
`fix/v0.107.1-compat` on the fork <https://github.com/tacertain/sts2-cli>. The
`act1` feature is deliberately **not** in that PR — it is ours, not a fix.

So the patch here may be partly redundant depending on when you pick this up:

- **PR still open** → apply the whole patch, as described above.
- **PR merged** → the two fixes are already in `main`; applying the full patch
  will conflict. Take only the `act1` hunks (the `StartRun` signature, the act
  list construction, and the `Program.cs` dispatch line).
- **PR rejected** → nothing changes; the full patch stands.

The working clone at `C:/Users/tacer/GitHub/sts2-cli` carries **uncommitted**
changes on `main` — the full set including `act1` — plus a `myfork` remote and
the `fix/v0.107.1-compat` branch. Do not `git checkout .` there expecting it to
be disposable. It is recoverable from this patch either way, but not obviously.

Exact environment this was built and validated against: game **v0.107.1**, commit
`59260271`, .NET SDK **9.0.317**, Windows.

## Running it

Build the TSV from the run history — restrict to the build the installed game
actually is, since map generation may differ between versions:

```python
import sys; sys.path.insert(0, "analysis")
import extract
CH = {"CHARACTER.IRONCLAD": "Ironclad", "CHARACTER.SILENT": "Silent",
      "CHARACTER.DEFECT": "Defect", "CHARACTER.NECROBINDER": "Necrobinder",
      "CHARACTER.REGENT": "Regent"}
for d in extract.raw_runs():
    if str(d.get("build_id")) != "v0.107.1" or d.get("game_mode") != "standard":
        continue
    pl = (d.get("players") or [{}])[0]
    print("\t".join([str(d["seed"]), CH[str(pl["character"])],
                     str(d.get("ascension", 0)), d["acts"][0].split(".")[-1]]))
```

Then `bash analysis/mapgen/genmaps.sh runs.tsv maps.jsonl`. Roughly 15 seconds
per run — 89 runs is about 20 minutes, so background it.

## Gotchas that cost time

- **One process per seed.** A second `start_run` fails with "State is already
  ...", and the following `get_map` returns the *first* run's map. Batching
  produces duplicate data with no error.
- **Rows are 1-based; the boss is row 16.** Act-1 floor `f` is map row `f - 1`.
  The first validation pass used the wrong offset and failed 88 of 89 runs while
  looking exactly like a data problem.
- **`get_map` skips empty rows**, so a row's index in the `rows` list is *not*
  its row number. Always read `node["row"]`.
- **`CreateForTest` is a test helper**, not the production run-start path. It
  worked here, but it is not guaranteed to match a real run in every respect.
- Regeneration only matches the build it was generated against. 59 of 148 runs
  are on v0.103.x and are **not** reproducible with a v0.107.1 install.

## Validating — do this after any game update

Never trust regenerated maps without it. Every walked node type must exist in the
corresponding map row:

```python
import sys; sys.path.insert(0, "analysis"); sys.path.insert(0, "analysis/mapgen")
import extract
from reconstruct import load_maps, validate
raw = {str(d.get("seed")): d for d in extract.raw_runs()}
print(validate(load_maps(), raw))     # expect (89, 89)
```

89 of 89 passed on v0.107.1. Anything less means the maps are not the ones the
player saw, and every conclusion drawn from them is void.
