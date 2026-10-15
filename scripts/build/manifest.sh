#!/usr/bin/env bash
set -Eeuo pipefail
export CC=${CC:-gcc-11} CXX=${CXX:-g++-11}
source "$(dirname -- "${BASH_SOURCE[0]}")/architecture.sh"
BUILD_JOBS=${BUILD_JOBS:-2}
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
RADIX_DIR=${RADIX_DIR:-/workspace/RadixGraph}
[[ $BUILD_JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be a positive integer' >&2; exit 2; }
python3 - "$GFE_DIR" "$RADIX_DIR" <<'PY'
import hashlib, json, os, subprocess, sys
from pathlib import Path
gfe, radix = map(Path, sys.argv[1:])
projects = {'gfe': gfe, 'radixgraph': radix, 'unodb': radix / 'unodb'}
for project in ('graphlog-base', 'teseo', 'sortledton', 'GTX-SIGMOD2025'):
    projects[project] = gfe / project
if os.environ['TARGETARCH'] == 'amd64':
    projects.update({name: gfe / name for name in ('junction', 'turf')})
manifest = {name: subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
            for name, path in projects.items()}
manifest['submodules'] = subprocess.check_output(['git', '-C', str(gfe), 'submodule', 'status', '--recursive'], text=True).splitlines()
manifest['architecture'] = os.environ['TARGETARCH']
manifest['available_systems'] = ['radixgraph', 'bvgt', 'teseo', 'sortledton', 'gtx']
manifest['excluded_systems'] = {}
if manifest['architecture'] == 'amd64':
    manifest['spruce_library_sha256'] = hashlib.sha256((gfe / 'libBVGT.a').read_bytes()).hexdigest()
else:
    manifest['available_systems'].remove('bvgt')
    manifest['excluded_systems']['bvgt'] = 'Spruce/bvgt requires x86_64'
    manifest['spruce_library_sha256'] = None
manifest['teseo_numa'] = False
manifest['compiler'] = subprocess.check_output([os.environ['CXX'], '--version'], text=True).splitlines()[0]
manifest['patches'] = ['Teseo non-NUMA thread contexts select allocated node zero', 'GAPBS queue/CAS atomics restored in standalone and GFE RadixGraph', 'graphlog full/single-element rightmost subtree bounds', 'GFE aging worker-zero partition restored and final operation counts logged', 'runtime standalone thread count', 'kernel-independent VmRSS field parsing', 'batch max-id bit length',
                       'transformation bounds guard and size arguments', 'slightly skewed workload distribution',
                       'GFE aging zero timeout is unlimited', 'GTX LCC honors the configured timeout']
if manifest['architecture'] == 'arm64':
    manifest['patches'] += ['ARM64 Teseo/Sortledton SIMD through SIMDe',
        'ARM64 monotonic epoch clock, sequentially consistent barriers and 128-bit atomics',
        'ARM64 Teseo queue/epoch/auxiliary-view publication and latch acquire ordering',
        'ARM64 Sortledton GC metadata synchronization and copied version snapshots',
        'ARM64 GTX portable prefetch and libatomic linkage', 'ARM64 Armv8-A compiler target']
Path('/opt/radixgraph-exp/source-versions.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
