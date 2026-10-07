#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
# The pinned project already defines BITS64. Do not delete a line by number.
cmake -S "$GFE_DIR/sortledton" -B "$GFE_DIR/sortledton/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$GFE_DIR/sortledton/build" --parallel "$BUILD_JOBS" --target sortledton

