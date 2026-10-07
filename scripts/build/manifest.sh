#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
python3 - "$GFE_DIR" "$RADIX_DIR" <<'PY'
import hashlib, json, os, subprocess, sys
from pathlib import Path
gfe, radix = map(Path, sys.argv[1:])
projects = {'gfe': gfe, 'radixgraph': radix, 'unodb': radix / 'unodb'}
for project in ('graphlog-base', 'junction', 'turf', 'teseo', 'sortledton', 'GTX-SIGMOD2025'):
    projects[project] = gfe / project
manifest = {name: subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
            for name, path in projects.items()}
manifest['submodules'] = subprocess.check_output(['git', '-C', str(gfe), 'submodule', 'status', '--recursive'], text=True).splitlines()
manifest['spruce_library_sha256'] = hashlib.sha256((gfe / 'libBVGT.a').read_bytes()).hexdigest()
manifest['teseo_numa'] = False
manifest['compiler'] = subprocess.check_output([os.environ['CXX'], '--version'], text=True).splitlines()[0]
manifest['patches'] = ['Teseo non-NUMA thread contexts select allocated node zero', 'GAPBS queue/CAS atomics restored in standalone and GFE RadixGraph', 'graphlog full/single-element rightmost subtree bounds', 'GFE aging worker-zero partition restored and final operation counts logged', 'runtime standalone thread count', 'kernel-independent VmRSS field parsing', 'batch max-id bit length',
                       'transformation bounds guard and size arguments', 'slightly skewed workload distribution']
Path('/opt/radixgraph-exp/source-versions.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
