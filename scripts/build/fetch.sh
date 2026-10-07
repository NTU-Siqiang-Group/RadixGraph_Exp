#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
checkout() {
    local url=$1 target=$2 revision=$3
    git init "$target"
    git -C "$target" remote add origin "$url"
    git -C "$target" fetch --depth 1 origin "$revision"
    git -C "$target" checkout --detach FETCH_HEAD
}

checkout https://github.com/ForwardStar/gfe_driver.git "$GFE_DIR" "$GFE_REV"
git -C "$GFE_DIR" submodule update --init --recursive

checkout https://github.com/ForwardStar/RadixGraph.git "$RADIX_DIR" "$RADIX_REV"
checkout https://github.com/laurynas-biveinis/unodb.git "$RADIX_DIR/unodb" "$UNODB_REV"

# Only the ART implementation is needed; UnoDB tests/fuzzers/benchmarks are off.
# Patch upstream harness defects, preserving paper defaults for every parameter.
python3 - "$RADIX_DIR" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
for name in ('test_radixgraph', 'test_vertex_index', 'test_batch_updates'):
    path = root / 'src' / (name + '.cpp')
    text = path.read_text().replace('#define NUM_THREADS 64', '''#include <cstdlib>
static const int NUM_THREADS = [] {
    const char* value = std::getenv("RG_THREADS");
    return value ? std::max(1, std::atoi(value)) : 64;
}();''')
    if name == 'test_batch_updates':
        text = text.replace('ceil(log2(max_id))', 'ceil(log2(max_id + 1))')
        text = text.replace('srand((int)time(NULL));', 'srand(42);')
        text = text.replace('{10, 100, 1000, 10000, 100000}', '{10, 100, 1000, 10000}')
    path.write_text(text)
path = root / 'src/test_transform_continuous.cpp'
text = path.read_text().replace('int main(int argc, char* argv[]) {', '''int main(int argc, char* argv[]) {
    const int n_max = argc > 1 ? std::stoi(argv[1]) : 10000000;
    const int n_start = argc > 2 ? std::stoi(argv[2]) : 100000;
    if (n_start <= 0 || n_max < n_start) return 2;''')
text = text.replace('for (int n = 100000; n <= 10000000; n++)',
                    'for (int n = n_start; n <= n_max; n++)')
text = text.replace('int l = n, r = 10000000;', 'int l = n, r = n_max;')
text = text.replace('i < 10000000;', 'i < n_max;')
text = text.replace('if (i % 100000 == 0) f << i', 'if ((i + 1) % std::max(1, n_max / 100) == 0) f << (i + 1)')
text = text.replace('if (i == results_n[now])', 'if (now < results_n.size() && i + 1 == results_n[now])')
path.write_text(text)
path = root / 'src/test_trie_workload.cpp'
text = path.read_text().replace('maximum /= 1.5; // reduce range to introduce slight skewness',
    'maximum /= 1.5; // reduce range to introduce slight skewness\n'
    '        distribution = std::uniform_int_distribution<unsigned long long>(0ull, maximum);')
path.write_text(text)
PY


checkout https://github.com/MordorsElite/graphlog-base.git "$GFE_DIR/graphlog-base" "$GRAPHLOG_REV"
git -C "$GFE_DIR/graphlog-base" submodule update --init --recursive
checkout https://github.com/preshing/junction.git "$GFE_DIR/junction" "$JUNCTION_REV"
checkout https://github.com/preshing/turf.git "$GFE_DIR/turf" "$TURF_REV"
checkout https://github.com/cwida/teseo.git "$GFE_DIR/teseo" "$TESEO_REV"
checkout https://gitlab.db.in.tum.de/per.fuchs/sortledton.git "$GFE_DIR/sortledton" "$SORTLEDTON_REV"
checkout https://github.com/Jiboxiake/GTX-SIGMOD2025.git "$GFE_DIR/GTX-SIGMOD2025" "$GTX_REV"
git -C "$GFE_DIR/GTX-SIGMOD2025" submodule update --init --recursive
"$CXX" "$RADIX_DIR/optimizer.cpp" -O3 -o "$RADIX_DIR/optimizer"
cp "$RADIX_DIR/optimizer" "$GFE_DIR/optimizer"
