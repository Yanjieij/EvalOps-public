#!/usr/bin/env bash
# fetch-hotpotqa.sh — download the HotpotQA dev set and slice a
# deterministic N-case subset into the dataset cache.
#
# Upstream: https://hotpotqa.github.io/
#   dev distractor set: ~47MB json
#   dev fullwiki set:   ~48MB json
#
# The full file is NOT committed to git — only the sliced YAML case
# files under datasets/hotpotqa-dev-<N>/ are. Run this script once after
# clone if you want to regenerate the subset. Default size is 100 cases,
# deterministic ordering so subsequent runs are reproducible.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
CACHE_DIR="${HOTPOTQA_CACHE:-$REPO_ROOT/.cache/hotpotqa}"
SIZE="${SIZE:-100}"
SPLIT="${SPLIT:-distractor}"   # distractor | fullwiki
OUT_DIR="$REPO_ROOT/datasets/hotpotqa-dev-$SIZE"

case "$SPLIT" in
  distractor)
    URL="http://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_distractor_v1.json"
    ;;
  fullwiki)
    URL="http://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_fullwiki_v1.json"
    ;;
  *)
    echo "unknown SPLIT=$SPLIT" >&2
    exit 2
    ;;
esac

mkdir -p "$CACHE_DIR" "$OUT_DIR/cases"
RAW="$CACHE_DIR/dev_${SPLIT}.json"

if [[ ! -s "$RAW" ]]; then
  echo "downloading $URL -> $RAW"
  curl -fL --retry 3 --retry-delay 2 -o "$RAW" "$URL"
fi

echo "slicing first $SIZE cases into $OUT_DIR/"
EVALOPS_PY="${EVALOPS_PY:-$HOME/miniconda3/envs/evalops/bin/python}"

"$EVALOPS_PY" - <<PY
import json, sys, pathlib
from evalops.datasets.hotpotqa import raw_to_case_dict
import yaml

src = pathlib.Path("$RAW")
out_dir = pathlib.Path("$OUT_DIR/cases")
size = int("$SIZE")

data = json.loads(src.read_text())[:size]
print(f"loaded {len(data)} raw entries from {src.name}")

out_dir.mkdir(parents=True, exist_ok=True)
# 25 cases per file for git-friendly diffs
chunk = 25
for i in range(0, len(data), chunk):
    payload = [raw_to_case_dict(row, benchmark_id="hotpotqa-dev") for row in data[i:i+chunk]]
    path = out_dir / f"cases_{i//chunk:03d}.yaml"
    with path.open("w") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)
    print(f"wrote {path} ({len(payload)} cases)")
PY

cat > "$OUT_DIR/benchmark.yaml" <<YAML
name: hotpotqa-dev-$SIZE
version: v0.1.0
description: |
  HotpotQA dev $SPLIT split, first $SIZE entries, sliced deterministically.
  Multi-hop QA with supporting-facts ground truth — exercises rag/f1,
  rag/citation_recall, and the llm/faithfulness judge on real data.
  See scripts/fetch-hotpotqa.sh to regenerate.
taxonomy_root: rag
labels:
  tier: public
  source: hotpotqa
  split: $SPLIT
  size: "$SIZE"
YAML

echo "wrote $OUT_DIR/benchmark.yaml"
echo ""
echo "Run:"
echo "  evalops run --benchmark datasets/hotpotqa-dev-$SIZE --sut mock --out runs/hotpot.json"
