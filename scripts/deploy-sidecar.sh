#!/usr/bin/env bash
# deploy-sidecar.sh — mirror the version-controlled Agent sidecar into the
# reference-app tree so it can be installed and run as an additive extension.
#
# Source of truth: evalops/sut-extensions/reference-agent-sidecar/
# Deploy target:   $REFERENCE_APP_HOME/services/agent-sidecar/   (default:
#                  ../reference-app/services/agent-sidecar/ relative to evalops)
#
# Usage:
#   scripts/deploy-sidecar.sh            # rsync source -> target
#   scripts/deploy-sidecar.sh --check    # exit 1 if source and target diverge
#   scripts/deploy-sidecar.sh --reinstall  # rsync + pip install -e the target

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
SRC="$REPO_ROOT/sut-extensions/reference-agent-sidecar"
TARGET_DEFAULT="$(cd "$REPO_ROOT/.." && pwd)/reference-app/services/agent-sidecar"
TARGET="${REFERENCE_APP_HOME:-$(cd "$REPO_ROOT/.." && pwd)/reference-app}/services/agent-sidecar"
# If REFERENCE_APP_HOME isn't set, fall back to the default inferred from the
# mono-repo layout.
if [[ -z "${REFERENCE_APP_HOME:-}" ]]; then
  TARGET="$TARGET_DEFAULT"
fi

mode="deploy"
for arg in "$@"; do
  case "$arg" in
    --check) mode="check" ;;
    --reinstall) mode="reinstall" ;;
    -h|--help)
      sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown flag: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "$SRC" ]]; then
  echo "source missing: $SRC" >&2
  exit 1
fi

case "$mode" in
  check)
    if [[ ! -d "$TARGET" ]]; then
      echo "target does not exist: $TARGET"
      exit 1
    fi
    if diff -rq \
        --exclude='__pycache__' \
        --exclude='*.egg-info' \
        --exclude='*.pyc' \
        "$SRC" "$TARGET" >/dev/null; then
      echo "OK: sidecar source and deploy target are in sync"
      exit 0
    else
      echo "DIVERGED:"
      diff -rq \
        --exclude='__pycache__' \
        --exclude='*.egg-info' \
        --exclude='*.pyc' \
        "$SRC" "$TARGET" || true
      exit 1
    fi
    ;;

  deploy|reinstall)
    mkdir -p "$TARGET"
    rsync -a --delete \
      --exclude='__pycache__' \
      --exclude='*.egg-info' \
      --exclude='*.pyc' \
      "$SRC/" "$TARGET/"
    echo "deployed $SRC -> $TARGET"

    if [[ "$mode" == "reinstall" ]]; then
      echo "reinstalling target as editable package..."
      pip install -e "$TARGET"
    fi
    ;;
esac
