#!/usr/bin/env bash
# Run the container's reproducibility pipeline. See --help and README.md.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export MPLBACKEND=Agg
exec python3 "$SCRIPT_DIR/scripts/pipeline.py" "$@"
