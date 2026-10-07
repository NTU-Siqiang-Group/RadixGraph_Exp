#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
cmake -S "$GFE_DIR/GTX-SIGMOD2025" -B "$GFE_DIR/GTX-SIGMOD2025/build" \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
cmake --build "$GFE_DIR/GTX-SIGMOD2025/build" --parallel "$BUILD_JOBS" --target gtx

