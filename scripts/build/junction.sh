#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
cmake -S "$GFE_DIR/junction" -B "$GFE_DIR/junction/build" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr/local -DJUNCTION_WITH_SAMPLES=OFF
cmake --build "$GFE_DIR/junction/build" --parallel "$BUILD_JOBS"
cmake --install "$GFE_DIR/junction/build"
ldconfig
curl --fail --location --retry 5 \
    https://github.com/Stardust-SJF/gfe_driver/releases/download/v2.0.0/libBVGT_stable.a \
    --output "$GFE_DIR/libBVGT.a"

printf "%s  %s\n" f625a9ecd9cdd4d100c82d273c63a7f3b85a62032ea734de37d130e4a60a60ee "$GFE_DIR/libBVGT.a" | sha256sum --check --status
