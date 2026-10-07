#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
build_driver() {
    local name=$1 system=$2 hops=$3 lib=$4
    local build="$GFE_DIR/builds/$name"
    local one=1 two=1
    [[ $hops != 1 ]] || two=0
    [[ $hops != 2 ]] || one=0
    sed -i -E \
        -e "s/^(#define[[:space:]]+RUN_GET_NEIGHBORS[[:space:]]+)[01]/\1$one/" \
        -e "s/^(#define[[:space:]]+RUN_TWO_HOP_NEIGHBORS[[:space:]]+)[01]/\1$two/" \
        "$GFE_DIR/configuration.hpp"
    mkdir -p "$build"
    # Configure first probes a C executable, before setting up C++ libraries.
    # Keep Spruce's C++ archive dependencies out of that probe.
    local extra_ldflags=''
    [[ $system != bvgt ]] || extra_ldflags='-L/usr/local/lib'
    (cd "$build"
     enable_mem_analysis=yes "$GFE_DIR/configure" --enable-optimize --disable-debug \
        "--with-$system=$lib" LDFLAGS="$extra_ldflags"
     # configure adds BVGT and junction. Turf must follow both static archives.
     if [[ $system == bvgt ]]; then
         printf '\nLDFLAGS += -lturf\n' >> Makefile
     fi
     make -j"$BUILD_JOBS")
    cp "$build/gfe_driver" "$GFE_DIR/build/gfe_driver_$name"
}
name=${1:?Expected system name}
system=$name
hops=both
case "$name" in
    radixgraph|gtx|bvgt|teseo|sortledton) ;;
    radixgraph_1hop|radixgraph_2hop|gtx_1hop|gtx_2hop)
        system=${name%_*}; hops=${name##*_}; hops=${hops%hop} ;;
    *) exit 2 ;;
esac
case "$system" in
    radixgraph) lib="$GFE_DIR/library/radixgraph/RadixGraph/build" ;;
    bvgt) lib="$GFE_DIR" ;;
    teseo) lib="$GFE_DIR/teseo/build" ;;
    sortledton) lib="$GFE_DIR/sortledton/build" ;;
    gtx) lib="$GFE_DIR/GTX-SIGMOD2025/build" ;;
esac
build_driver "$name" "$system" "$hops" "$lib"
