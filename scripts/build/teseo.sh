#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
# Headers may expose host NUMA APIs even when Teseo is configured without NUMA.
# In that mode its auxiliary view array has one element, so thread contexts
# must consistently select node zero on hosts with multiple NUMA nodes.
python3 - "$GFE_DIR/teseo/src/context/thread_context.cpp" <<'PYFIX'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
old = 'm_cache_numa_thread = util::Thread::get_numa_id();'
if text.count(old) != 1:
    raise SystemExit('Teseo NUMA thread assignment changed; inspect non-NUMA allocation bounds')
header = '#include "teseo/context/static_configuration.hpp"'
if header not in text:
    text = header + '\n' + text
text = text.replace(old, 'm_cache_numa_thread = StaticConfiguration::numa_enabled ? util::Thread::get_numa_id() : 0;')
path.write_text(text)
PYFIX
(cd "$GFE_DIR/teseo" && autoreconf -iv)
mkdir -p "$GFE_DIR/teseo/build"
# Docker build cannot reliably detect the NUMA topology of the runtime host.
# Teseo's non-NUMA mode is portable; record this choice in build provenance.
(cd "$GFE_DIR/teseo/build" && ../configure --enable-optimize --disable-debug --disable-numa && make -j"$BUILD_JOBS")
