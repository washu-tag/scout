#!/usr/bin/env bash
#
# Per-commit change volume for a Delta table, read straight from _delta_log.
# No Trino, no OPA, no Spark -- just mc inside the MinIO tenant pod.
#
#   ./commit-sizes.sh                  # list available tables, then exit
#   ./commit-sizes.sh reports          # last 200 commits of `reports`
#   ./commit-sizes.sh reports 50       # last 50 commits
#
# <table> is the Delta table directory name, i.e. `report_delta_table_name` from
# inventory (default `reports`). Run with no args if unsure.
#
# This reads exact operationMetrics, so prefer it wherever the commit JSONs still
# exist. Delta log retention prunes them; use write-events.sh to reconstruct
# older history from data-file mtimes.
#
# Pod and warehouse root are discovered from the cluster. Override the root with:
#   SCOUT_DELTA_ROOT=mybucket/myprefix ./commit-sizes.sh reports
set -euo pipefail

TABLE="${1:-}"
N="${2:-200}"

POD_NS=$(kubectl get pods -A -l v1.min.io/tenant -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null || true)
POD=$(kubectl get pods -A -l v1.min.io/tenant -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[[ -n "$POD" ]] || { echo "No MinIO tenant pod found (label v1.min.io/tenant). Wrong context?" >&2; exit 1; }

if [[ -n "${SCOUT_DELTA_ROOT:-}" ]]; then
  ROOT="$SCOUT_DELTA_ROOT"
else
  ROOT=$(kubectl get cm -A --field-selector metadata.name=spark-defaults-extractor \
           -o jsonpath='{.items[0].data.spark-defaults\.conf}' 2>/dev/null |
         awk '$1=="spark.sql.warehouse.dir"{print $2}' | sed -E 's#^[a-z0-9]+://##')
fi
[[ -n "$ROOT" ]] || { echo "Could not resolve the warehouse root; set SCOUT_DELTA_ROOT=<bucket>/<prefix>." >&2; exit 1; }

MC='set -a; . "$MINIO_CONFIG_ENV_FILE"; set +a; export MC_HOST_l="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@localhost:9000";'
# stderr is deliberately NOT suppressed: an Access Denied or a bad prefix must
# surface rather than turn into a misleading "no such table".
_mc() { kubectl exec -n "$POD_NS" "$POD" -c minio -- sh -c "$MC $1"; }

echo "pod=$POD_NS/$POD  warehouse=$ROOT"

if [[ -z "$TABLE" ]]; then
  echo "Delta tables under $ROOT/ (pass one as the first argument):"
  _mc "mc ls l/$ROOT/" | awk '{print "  " $NF}'
  exit 0
fi

mapfile -t VERS < <(_mc "mc ls --json l/$ROOT/$TABLE/_delta_log/" |
  jq -r 'select((.key // "") | endswith(".json")) | .key' | sed 's/\.json$//' | sort | tail -"$N")

if (( ${#VERS[@]} == 0 )); then
  echo >&2
  echo "No commit JSON under $ROOT/$TABLE/_delta_log/." >&2
  echo "Raw listing of the table root (errors included):" >&2
  _mc "mc ls l/$ROOT/$TABLE/" >&2 || true
  echo "Raw listing of _delta_log/ (errors included):" >&2
  _mc "mc ls l/$ROOT/$TABLE/_delta_log/" >&2 || true
  exit 1
fi
echo "table=$TABLE  commits sampled=${#VERS[@]}"

TSV=$(mktemp); trap 'rm -f "$TSV"' EXIT
_mc "for v in ${VERS[*]}
     do echo \"#VER \$v\"; mc cat l/$ROOT/$TABLE/_delta_log/\$v.json 2>/dev/null
     done" |
awk '/^#VER /{ver=$2+0; next} /"commitInfo"/{print ver "\t" $0}' |
jq -R -r 'split("\t") | .[0] as $v | (.[1]|fromjson) | select(.commitInfo) | .commitInfo as $c |
  [ $v, $c.operation,
    ($c.operationMetrics.numTargetBytesAdded   // $c.operationMetrics.numOutputBytes // "0"),
    ($c.operationMetrics.numTargetFilesAdded   // $c.operationMetrics.numFiles       // "0"),
    ($c.operationMetrics.numTargetRowsInserted // $c.operationMetrics.numOutputRows  // "0"),
    ($c.operationMetrics.numTargetRowsUpdated  // "0"),
    ($c.operationMetrics.numTargetChangeFilesAdded // "0") ] | @tsv' > "$TSV"

awk -F'\t' '
  BEGIN{printf "\n%-5s %-9s %13s %6s %9s %9s %7s %7s\n",
        "ver","op","bytes","files","rowsIns","rowsUpd","cdcFls","B/row"}
  { r=$5+$6
    printf "%-5s %-9s %13d %6s %9d %9d %7s %7s\n",
      $1,$2,$3,$4,$5,$6,$7,(r?sprintf("%.0f",$3/r):"-") }' "$TSV"

awk -F'\t' '$3+0 > 0 {print $3}' "$TSV" | sort -n | awk '
  {a[NR]=$1; s+=$1}
  END{ if(NR==0){print "\nno data-bearing commits"; exit}
       i=int(NR*0.95); if(i<1) i=1
       printf "\ndata commits=%d  mean=%.2f MiB  p95=%.2f MiB  max=%.2f MiB\n",
              NR, s/NR/1048576, a[i]/1048576, a[NR]/1048576 }'
printf "files-per-commit seen: %s\n" "$(awk -F'\t' '$3+0>0{print $4}' "$TSV" | sort -un | paste -sd, -)"
printf "commits carrying CDC files (update-bearing, admitted whole): %s of %s\n" \
  "$(awk -F'\t' '$7+0>0' "$TSV" | wc -l | tr -d ' ')" \
  "$(awk -F'\t' '$3+0>0' "$TSV" | wc -l | tr -d ' ')"
