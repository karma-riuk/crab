#!/usr/bin/env bash
# stats_by_failure.sh — usage: ./stats_by_failure.sh dataset.multithread.json

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <dataset_json>"
  exit 1
fi

FILE="$1"

# total number of entries
total=$(jq '.entries | length' "$FILE")

# emit count, raw percentage, reason → format & align in awk
jq -r --argjson total "$total" '
  .entries
  | map(.metadata.reason_for_failure // "UNKNOWN")
  | group_by(.)
  | map({ reason: .[0], count: length })
  | map(. + { pct: (.count / $total * 100) })
  | sort_by(.pct) | reverse
  | .[]
  | "\(.count)\t\(.pct)\t\(.reason)"
' "$FILE" | awk -F'\t' '
{
  lines[NR] = $1 "\t" $2 "\t" $3
}
END {
  total=0
  for (i = 1; i <= NR; i++) {
    split(lines[i], f, "\t")
    total += f[1]
    printf "%4d - %5.2f%% --  %s\n", f[1], f[2], f[3]
  }

  printf "----\n"
  printf "%4d\n", total
  printf "\n"

  total=0
  for (i = 1; i <= NR; i++) {
    split(lines[i], f, "\t")
    if (f[3] ~ /^[[:blank:]]*Valid/){
        total += f[1]
        printf "%4d - %5.2f%% --  %s\n", f[1], f[2], f[3]
    }
  }
  printf "----\n"
  printf "%4d\n", total
}'
