#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
# Upstream represents a full rightmost subtree as remainder zero and a
# single-element subtree as height zero; both recurse into invalid arithmetic.
python3 - "$GFE_DIR/graphlog-base/counting_tree.cpp" <<'PYFIX'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
old = 'uint64_t rightmost_subtree_sz = num_entries % subtree_sz;'
if old not in text:
    raise SystemExit('Graphlog counting tree changed; inspect rightmost subtree bounds')
text = text.replace('#include <cassert>', '#include <algorithm>\n#include <cassert>')
text = text.replace('int height = ceil(log2(m_num_entries) / log2(m_node_size));',
                    'int height = std::max(1, static_cast<int>(ceil(log2(m_num_entries) / log2(m_node_size))));')
text = text.replace(old, 'uint64_t rightmost_subtree_sz = ((num_entries - 1) % subtree_sz) + 1;')
text = text.replace('(num_entries / subtree_sz) + (rightmost_subtree_sz != 0)',
                    '((num_entries - 1) / subtree_sz) + 1')
text = text.replace('rightmost_subtree_height = ceil(log2(rightmost_subtree_sz) / log2(m_node_size));',
                    'rightmost_subtree_height = height > 1 ? std::max(1, static_cast<int>(ceil(log2(rightmost_subtree_sz) / log2(m_node_size)))) : 0;')
path.write_text(text)
PYFIX
cmake -S "$GFE_DIR/graphlog-base" -B "$GFE_DIR/graphlog-base/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$GFE_DIR/graphlog-base/build" --parallel "$BUILD_JOBS"

