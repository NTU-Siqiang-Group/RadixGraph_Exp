#!/usr/bin/env bash
set -euo pipefail

# Build pinned upstream libraries with native batch APIs, using schedulers
# supported by current GCC instead of requiring a historical Cilk compiler.
batch_root=${1:-/workspace/batch}
script_dir=${2:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}
terrace_revision=${TERRACE_REVISION:-2d158f42855f10f6091bcf69ebde0bc2ba207d1c}
aspen_revision=${ASPEN_REVISION:-ecc3193da05aef3b4e5f5de7cab77b215c0b8211}
cpam_revision=${CPAM_REVISION:-049ad573ef5844a7f4e59c92111749df1da7b6ba}
mkdir -p "$batch_root"

fetch() {
    local name=$1 url=$2 revision=$3
    if [[ ! -d "$batch_root/$name/.git" ]]; then
        git clone --no-checkout "$url" "$batch_root/$name"
    fi
    git -C "$batch_root/$name" fetch --depth 1 origin "$revision"
    git -C "$batch_root/$name" checkout --detach FETCH_HEAD
}

fetch terrace https://github.com/PASSIONLab/terrace.git "$terrace_revision"
fetch aspen https://github.com/ldhulipala/aspen.git "$aspen_revision"
fetch cpam https://github.com/ParAlg/CPAM.git "$cpam_revision"
git -C "$batch_root/cpam" submodule update --init --depth 1

# The upstream diff encoder assigns nested map values into raw storage. That
# invokes map::clear() on an uninitialized pointer. Construct these objects,
# as upstream's default encoder already does, rather than assigning to them.
python3 - "$batch_root/cpam/include/cpam/compression.h" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
for index in ("0", "i"):
    old = f"vals[{index}] = Entry::get_val(data[{index}]);"
    new = f"parlay::assign_uninitialized(vals[{index}], Entry::get_val(data[{index}]));"
    if text.count(old) == 2:
        text = text.replace(old, new)
    elif text.count(new) != 2:
        raise SystemExit("CPAM encoder changed; review initialization compatibility patch")
path.write_text(text)
PY

g++ -O3 -DNDEBUG -std=c++17 -fopenmp -DOPENMP=1 -DCILK=0 -Dcilk_for=parallel_for \
    -I"$batch_root/terrace" -I"$batch_root/terrace/include" -I"$script_dir/batch" \
    "$script_dir/batch/terrace.cpp" \
    "$batch_root/terrace/src/PMA.cc" "$batch_root/terrace/src/PMA_Lock.cc" \
    "$batch_root/terrace/src/partitioned_counter.c" "$batch_root/terrace/src/util.cc" \
    -pthread -lssl -lcrypto -o "$batch_root/terrace/batch_benchmark"

g++ -O3 -DNDEBUG -std=c++17 -DEDGELONG -mcx16 -DHOMEGROWN -DUSEMALLOC \
    -I"$batch_root/aspen/code" -I"$script_dir/batch" "$script_dir/batch/aspen.cpp" \
    -pthread -ldl -o "$batch_root/aspen/batch_benchmark"

g++ -O3 -DNDEBUG -std=c++17 -mcx16 -DHOMEGROWN -DUSE_DIFF_ENCODING \
    -I"$batch_root/cpam/parlaylib/include" -I"$batch_root/cpam/include" \
    -I"$batch_root/cpam/examples/graphs" -I"$script_dir/batch" \
    "$script_dir/batch/cpam.cpp" -pthread -ljemalloc -o "$batch_root/cpam/batch_benchmark"

python3 - "$batch_root" <<'PY'
import json
from pathlib import Path
import subprocess
import sys
root = Path(sys.argv[1])
versions = {name: subprocess.check_output(["git", "-C", str(root / name), "rev-parse", "HEAD"], text=True).strip()
            for name in ("terrace", "aspen", "cpam")}
versions["parlaylib"] = subprocess.check_output(["git", "-C", str(root / "cpam/parlaylib"), "rev-parse", "HEAD"], text=True).strip()
versions["patches"] = ["CPAM diff encoder: construct values in uninitialized storage with parlay::assign_uninitialized"]
(root / "source-versions.json").write_text(json.dumps(versions, indent=2) + "\n")
PY
