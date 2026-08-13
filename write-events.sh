#!/usr/bin/env bash
#
# Reconstruct per-commit write volume for a Delta table from the DATA FILES,
# so it still works where Delta log retention has pruned the commit JSONs.
#
#   ./write-events.sh reports          # 30 biggest write events
#   ./write-events.sh reports 100      # 100 biggest
#   GAP=30 ./write-events.sh reports   # widen the clustering window
#
# Files written by one commit share a modification timestamp to within seconds,
# so clustering objects by mtime recovers commit boundaries.
#
# Calibration: against two tables whose commit JSONs still survive, GAP=10
# reproduced the data-bearing commit count exactly (16/16 and 6/6 -- the extra
# JSON in each is v0 CREATE, which writes no data files), and per-event byte
# totals and file counts matched numTargetBytesAdded / numTargetFilesAdded.
# GAP=30 already over-merged commits that landed ~25s apart.
#
# Caveats worth holding while reading the output:
#   * A MERGE rewrites the files it touches, so an event's bytes are "bytes
#     written by that commit" -- the number we want, but a rewrite-heavy commit
#     looks bigger than the new data it carried.
#   * Tombstoned files survive until VACUUM. Where VACUUM has run, older events
#     are partly or entirely missing.
#   * An OPTIMIZE appears as a write event but is not an ingest.
#   * Two commits landing within GAP of each other merge into one event, so an
#     event size is an UPPER bound on a single commit.
set -euo pipefail

TABLE="${1:?usage: $0 <table> [top-n]}"
TOP="${2:-30}"
GAP="${GAP:-10}"

POD_NS=$(kubectl get pods -A -l v1.min.io/tenant -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null || true)
POD=$(kubectl get pods -A -l v1.min.io/tenant -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[[ -n "$POD" ]] || { echo "No MinIO tenant pod found (label v1.min.io/tenant)." >&2; exit 1; }

if [[ -n "${SCOUT_DELTA_ROOT:-}" ]]; then
  ROOT="$SCOUT_DELTA_ROOT"
else
  ROOT=$(kubectl get cm -A --field-selector metadata.name=spark-defaults-extractor \
           -o jsonpath='{.items[0].data.spark-defaults\.conf}' 2>/dev/null |
         awk '$1=="spark.sql.warehouse.dir"{print $2}' | sed -E 's#^[a-z0-9]+://##')
fi
[[ -n "$ROOT" ]] || { echo "Could not resolve the warehouse root; set SCOUT_DELTA_ROOT=<bucket>/<prefix>." >&2; exit 1; }

MC='set -a; . "$MINIO_CONFIG_ENV_FILE"; set +a; export MC_HOST_l="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@localhost:9000";'
_mc() { kubectl exec -n "$POD_NS" "$POD" -c minio -- sh -c "$MC $1"; }

echo "pod=$POD_NS/$POD  warehouse=$ROOT  table=$TABLE  gap=${GAP}s"

# --- how much exact history survives? ---------------------------------------
JSONS=""
while IFS= read -r j; do JSONS="$JSONS $j"; done < <(
  _mc "mc ls --json l/$ROOT/$TABLE/_delta_log/" 2>/dev/null |
  jq -r 'select((.key // "") | endswith(".json")) | .key' | sed 's/\.json$//' | sort)
# shellcheck disable=SC2086
set -- $JSONS
if [ "$#" -gt 0 ]; then
  eval "last=\${$#}"
  echo "exact commit JSONs surviving: $# (v$((10#$1)) .. v$((10#$last)))"
  echo "  -> for those versions prefer commit-sizes.sh, which reads real metrics"
else
  echo "exact commit JSONs surviving: none (all pruned by log retention)"
fi
echo "everything below is reconstructed from data-file mtimes"
echo

TSV=$(mktemp); trap 'rm -f "$TSV"' EXIT
_mc "mc ls --recursive --json l/$ROOT/$TABLE/" 2>/dev/null |
jq -r 'select((.key // "") | endswith(".parquet"))
       | select((.key | startswith("_delta_log/")) | not)
       | [ (.lastModified | sub("\\.[0-9]+Z$";"Z") | fromdateiso8601),
           .size,
           ((.key | capture("year=(?<y>[0-9]+)") | .y) // "unpartitioned"),
           (.lastModified | sub("\\.[0-9]+Z$";"Z") | sub("T";" ") | sub("Z";"")) ] | @tsv' |
sort -n |
awk -F'\t' -v gap="$GAP" '
  function flush(){ if(n==0) return
                    printf "%s\t%d\t%d\t%.2f\t%s\n", startiso, n, ny, bytes/1048576, ys
                    n=0; bytes=0; ny=0; ys=""; startiso="" }
  { if (prev!="" && $1-prev > gap) flush()
    n++; bytes+=$2
    if (index("," ys ",", "," $3 ",") == 0) { ys = (ys=="" ? $3 : ys "," $3); ny++ }
    if (startiso=="") startiso=$4
    prev=$1 }
  END{ flush() }' > "$TSV"

echo "write events reconstructed: $(wc -l < "$TSV" | tr -d ' ')"
echo
printf "%-20s %7s %7s %10s  %s\n" "when (UTC)" "files" "years" "MiB" "year partitions"
sort -t$'\t' -k4 -rn "$TSV" | head -"$TOP" |
while IFS=$'\t' read -r ts n ny mib ys; do
  printf "%-20s %7s %7s %10s  %s\n" "$ts" "$n" "$ny" "$mib" "$ys"
done

echo
cut -f4 "$TSV" | sort -n | awk '
  {a[NR]=$1; s+=$1}
  END{ if(NR==0){print "no events"; exit}
       p=int(NR*0.5); if(p<1)p=1; q=int(NR*0.95); if(q<1)q=1
       printf "event size MiB: n=%d total=%.1f median=%.2f p95=%.2f max=%.2f\n", NR, s, a[p], a[q], a[NR] }'
printf "max files in one event: %s\n" "$(cut -f2 "$TSV" | sort -n | tail -1)"
printf "max year partitions in one event: %s\n" "$(cut -f3 "$TSV" | sort -n | tail -1)"
