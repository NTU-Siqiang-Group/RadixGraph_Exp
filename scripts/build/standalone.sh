#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
# Header switches are compile-time choices. Each source copy and object tree is
# isolated, preventing CMake or Make from reusing objects from another variant.
mkdir -p "$RADIX_DIR/variants" "$RADIX_DIR/builds"
variant=${1:?Expected variant sort, art, sort-no-chain, or index}
case "$variant" in sort|art|sort-no-chain|index) ;; *) exit 2 ;; esac
    src="$RADIX_DIR/variants/$variant"
    mkdir -p "$src"
    cp "$RADIX_DIR/CMakeLists.txt" "$src/"
    cp -a "$RADIX_DIR/src" "$src/src"
    python3 "$(dirname -- "${BASH_SOURCE[0]}")/patch-atomics.py" "$src/src/GAPBS/platform_atomics.h"
    # /proc/status field ordering is kernel-dependent; measure VmRSS by name.
    python3 - "$src/src/utils.cpp" <<'PYFIX'
from pathlib import Path
import re, sys
path = Path(sys.argv[1])
text = path.read_text()
pattern = r'unsigned int get_proc_mem\(\) \{.*?\n\}'
replacement = 'unsigned int get_proc_mem() {\n    std::ifstream status("/proc/self/status");\n    std::string line;\n    while (std::getline(status, line)) {\n        if (line.rfind("VmRSS:", 0) == 0) {\n            return static_cast<unsigned int>(std::stoul(line.substr(6)));\n        }\n    }\n    return 0;\n}'
text, count = re.subn(pattern, lambda _: replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit('Standalone RSS reader changed; inspect memory instrumentation')
path.write_text(text)
PYFIX
    ln -s "$RADIX_DIR/unodb" "$src/unodb"
    use_sort=1 use_art=0 edge_chain=1
    case "$variant" in
        art) use_sort=0; use_art=1 ;;
        sort-no-chain) edge_chain=0 ;;
        index) use_art=1 ;;
    esac
    sed -i -E \
        -e "s/^(#define USE_SORT) [01]/\1 $use_sort/" \
        -e "s/^(#define USE_ART) [01]/\1 $use_art/" \
        -e "s/^(#define USE_EDGE_CHAIN) [01]/\1 $edge_chain/" "$src/src/radixgraph.h"
    cmake -S "$src" -B "$RADIX_DIR/builds/$variant" -DCMAKE_BUILD_TYPE=Release \
        -DSTATS=OFF -DTESTS=OFF -DBENCHMARKS=OFF -DSTANDALONE=OFF
    if [[ $variant == index ]]; then
        cmake --build "$RADIX_DIR/builds/$variant" --parallel "$BUILD_JOBS" --target test_vertex_index
    elif [[ $variant == art ]]; then
        cmake --build "$RADIX_DIR/builds/$variant" --parallel "$BUILD_JOBS" --target test_radixgraph
    elif [[ $variant == sort-no-chain ]]; then
        cmake --build "$RADIX_DIR/builds/$variant" --parallel "$BUILD_JOBS" --target test_analytics
    else
        cmake --build "$RADIX_DIR/builds/$variant" --parallel "$BUILD_JOBS" --target \
            test_radixgraph test_trie test_trie_workload test_transform_continuous \
            test_analytics test_batch_updates
    fi
