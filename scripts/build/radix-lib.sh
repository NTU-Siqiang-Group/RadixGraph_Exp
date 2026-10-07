#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
# The GFE RadixGraph submodule has no ART dependency or switches applied.
python3 "$(dirname -- "${BASH_SOURCE[0]}")/patch-atomics.py" \
    "$GFE_DIR/library/radixgraph/RadixGraph/src/GAPBS/platform_atomics.h"
cmake -S "$GFE_DIR/library/radixgraph/RadixGraph" \
    -B "$GFE_DIR/library/radixgraph/RadixGraph/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$GFE_DIR/library/radixgraph/RadixGraph/build" --parallel "$BUILD_JOBS" --target RG
"$CXX" "$GFE_DIR/scripts/create_vertex_ops.cpp" -O3 -o "$GFE_DIR/create_vertex_ops"
"$CXX" "$GFE_DIR/scripts/generate_property_files.cpp" -O3 -o "$GFE_DIR/generate_property_files"
