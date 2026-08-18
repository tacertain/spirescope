#!/bin/bash
# Regenerate the act-1 map for each real run, one JSON line per run.
#
# Usage:  bash analysis/mapgen/genmaps.sh runs.tsv out.jsonl
#   runs.tsv columns: seed <TAB> character <TAB> ascension <TAB> variant
#   Produce it with `python_run` — see mapgen/README.md.
#
# ONE PROCESS PER SEED. A second start_run in the same process fails with
# "State is already ..."; the following get_map then returns the FIRST run's
# map, so batching silently yields duplicate data rather than an error.
set -u

CLI="${STS2_CLI:-C:/Users/tacer/GitHub/sts2-cli}"
IN="${1:?usage: genmaps.sh runs.tsv out.jsonl}"
OUT="${2:?usage: genmaps.sh runs.tsv out.jsonl}"
export PATH="/c/Program Files/dotnet:$PATH"

if [ ! -d "$CLI" ]; then
    echo "sts2-cli not found at $CLI — see mapgen/README.md, and apply" >&2
    echo "analysis/mapgen/sts2-cli-v0.107.1.patch after cloning." >&2
    exit 1
fi

: > "$OUT"
n=0
while IFS=$'\t' read -r seed char asc variant; do
    [ -z "${seed:-}" ] && continue
    n=$((n + 1))
    line=$(printf '%s\n%s\n' \
        "{\"cmd\":\"start_run\",\"character\":\"$char\",\"seed\":\"$seed\",\"ascension\":$asc}" \
        '{"cmd":"get_map"}' \
        | timeout 120 dotnet run --project "$CLI/src/Sts2Headless/Sts2Headless.csproj" --no-build 2>/dev/null \
        | grep '^{' | tail -1)
    [ -z "$line" ] && line='{"type":"error","message":"no output"}'
    printf '{"seed":"%s","character":"%s","ascension":%s,"variant":"%s","map":%s}\n' \
        "$seed" "$char" "$asc" "$variant" "$line" >> "$OUT"
    echo "[$n] $seed $variant"
done < "$IN"
echo "DONE $n runs -> $OUT"
