#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
# Restore the missing modulo partition and expose actual processed counts.
# Keep benchmark corrections here, after reusable dependency build layers.
python3 - "$GFE_DIR/experiment/details/aging2_master.cpp" <<'PYFIX'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
old = 'for(uint64_t worker_id = 1; worker_id < parameters().m_num_threads; worker_id++)'
if old not in text:
    raise SystemExit('GFE worker loop changed; inspect update partitioning before building')
text = text.replace(old, 'for(uint64_t worker_id = 0; worker_id < parameters().m_num_threads; worker_id++)')
anchor = '    build_service.stop();'
if text.count(anchor) != 1:
    raise SystemExit('GFE completion hook changed; inspect operation-count instrumentation')
text = text.replace(anchor, anchor + '\n    LOG("[Pipeline] Operations performed: " << m_num_operations_performed.load() << " / " << num_operations_total());')
path.write_text(text)
PYFIX
(cd "$GFE_DIR" && autoreconf -iv)
mkdir -p "$GFE_DIR/build" "$GFE_DIR/builds"
