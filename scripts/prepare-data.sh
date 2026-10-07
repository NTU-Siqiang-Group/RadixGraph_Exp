#!/usr/bin/env bash
# Download/prepare reusable inputs without putting datasets in image layers.
set -Eeuo pipefail
GFE_DIR=${GFE_DIR:-/workspace/gfe_driver}
DATA_DIR=${DATA_DIR:-/data}
PROFILE=${PROFILE:-paper}
while (($#)); do
    case $1 in
        --profile) PROFILE=$2; shift 2 ;;
        --data-dir) DATA_DIR=$2; shift 2 ;;
        --help|-h)
            echo 'Usage: prepare-data.sh [--profile paper|smoke] [--data-dir PATH]'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
[[ $PROFILE == paper || $PROFILE == smoke ]] || { echo 'PROFILE must be paper or smoke' >&2; exit 2; }
mkdir -p "$DATA_DIR"
DATA_DIR=$(realpath "$DATA_DIR")
export DATA_DIR GFE_DIR PROFILE
if [[ -f $DATA_DIR/.profile && $(cat "$DATA_DIR/.profile") != "$PROFILE" ]]; then
    echo "Data cache belongs to profile $(cat "$DATA_DIR/.profile"); use a separate directory for $PROFILE" >&2
    exit 2
fi
printf '%s\n' "$PROFILE" > "$DATA_DIR/.profile"

if [[ $PROFILE == paper ]]; then
    mkdir -p "$DATA_DIR/downloads" "$DATA_DIR/tmp"
    fetch() {
        local url=$1 destination=$2
        if [[ ! -f $destination ]]; then
            # Resume interrupted downloads and publish only a completed file.
            wget --continue --tries=10 --timeout=60 --output-document="$destination.part" "$url"
            mv "$destination.part" "$destination"
        fi
    }
    archive_needed=0
    for name in graph500-24 uniform-24 dota-league; do
        for extension in properties e v; do
            [[ -s $DATA_DIR/$name.$extension ]] || archive_needed=1
        done
    done
    if [[ $archive_needed == 1 ]]; then
        fetch https://zenodo.org/records/3966439/files/datasets.tar.gz "$DATA_DIR/downloads/datasets.tar.gz"
        # Extract only the three paper graphs. The archive also holds scale 26.
        python3 - "$DATA_DIR" <<'PY'
import sys, tarfile
from pathlib import Path
root = Path(sys.argv[1])
names = ('graph500-24', 'uniform-24', 'dota-league')
wanted = {name + extension for name in names for extension in ('.properties', '.e', '.v')}
with tarfile.open(root / 'downloads/datasets.tar.gz', 'r|gz') as archive:
    for member in archive:
        base = Path(member.name).name
        if member.isfile() and base in wanted:
            destination = root / base
            if not destination.exists() or destination.stat().st_size != member.size:
                source = archive.extractfile(member)
                temporary = destination.with_name(destination.name + '.part')
                with temporary.open('wb') as output:
                    import shutil
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                temporary.replace(destination)
PY
    fi
    for name in com-lj.ungraph com-orkut.ungraph twitter-2010; do
        if [[ ! -s $DATA_DIR/$name.el ]]; then
            if [[ $name == twitter-2010 ]]; then
                url=https://snap.stanford.edu/data/twitter-2010.txt.gz
            else
                url="https://snap.stanford.edu/data/bigdata/communities/$name.txt.gz"
            fi
            fetch "$url" "$DATA_DIR/downloads/$name.txt.gz"
            gzip -cd "$DATA_DIR/downloads/$name.txt.gz" | \
                awk '!/^#/ && NF >= 2 {print $1, $2}' > "$DATA_DIR/$name.el.part"
            mv "$DATA_DIR/$name.el.part" "$DATA_DIR/$name.el"
        fi
    done
    if [[ ! -f $DATA_DIR/.twitter-deduplicated ]]; then
        # Match the notebook's undirected canonicalization. External sorting
        # bounds RAM even for Twitter, unlike its std::set implementation.
        awk '{if ($1 <= $2) print $1, $2; else print $2, $1}' "$DATA_DIR/twitter-2010.el" | \
            LC_ALL=C sort --parallel="${SORT_JOBS:-2}" --buffer-size="${SORT_MEMORY:-4G}" \
                --temporary-directory="$DATA_DIR/tmp" --key=1,1n --key=2,2n --unique > "$DATA_DIR/twitter-2010.el.part"
        mv "$DATA_DIR/twitter-2010.el.part" "$DATA_DIR/twitter-2010.el"
        touch "$DATA_DIR/.twitter-deduplicated"
    fi
    for name in com-lj.ungraph com-orkut.ungraph twitter-2010; do
        if [[ ! -f $DATA_DIR/.$name-properties-ready ]]; then
            # Generate into a scratch directory so interrupted processing does
            # not leave final files that look like a complete cache.
            staging="$DATA_DIR/tmp/$name-properties"
            mkdir -p "$staging"
            ln -sf "$DATA_DIR/$name.el" "$staging/$name.el"
            "$GFE_DIR/generate_property_files" "$staging/$name.el"
            for extension in properties e v; do
                [[ -s $staging/$name.$extension ]] || { echo "Empty generated $name.$extension" >&2; exit 1; }
                mv "$staging/$name.$extension" "$DATA_DIR/$name.$extension"
            done
            touch "$DATA_DIR/.$name-properties-ready"
        fi
    done
else
    # Deterministic tiny fixtures exercise every task and exporter. They are
    # explicitly marked smoke and never stand in for paper measurements.
    python3 - "$DATA_DIR" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
names = ('graph500-24', 'uniform-24', 'dota-league', 'com-lj.ungraph', 'com-orkut.ungraph', 'twitter-2010')
n = 256
edges = sorted({tuple(sorted((i, (i + step) % n))) for i in range(n) for step in (1, 3, 7, 15)})
for name in names:
    if (root / (name + '.properties')).exists():
        continue
    (root / (name + '.el')).write_text(''.join(f'{u} {v}\n' for u, v in edges))
    (root / (name + '.e')).write_text(''.join(f'{u} {v} {((u + v) % 100 + 1) / 100:.2f}\n' for u, v in edges))
    (root / (name + '.v')).write_text(''.join(f'{i}\n' for i in range(n)))
    values = {'vertex-file': name + '.v', 'edge-file': name + '.e', 'meta.vertices': n,
              'meta.edges': len(edges), 'directed': 'false', 'edge-properties.names': 'weight',
              'edge-properties.types': 'real', 'algorithms': 'bfs, cdlp, lcc, pr, sssp, wcc, bc',
              'bfs.source-vertex': 0, 'cdlp.max-iterations': 10, 'pr.damping-factor': .85,
              'pr.num-iterations': 10, 'sssp.weight-property': 'weight', 'sssp.source-vertex': 0,
              'bc.max-iterations': 5}
    (root / (name + '.properties')).write_text('# SMOKE FIXTURE: not a paper dataset\n' +
        ''.join(f'graph.{name}.{key} = {value}\n' for key, value in values.items()))
PY
fi

# Preserve graph metadata/source IDs supplied by Graphalytics; add BC and
# normalize archive filenames instead of passing *.e to the *.el-only tool.
python3 - "$DATA_DIR" <<'PY'
import re, sys
from pathlib import Path
root = Path(sys.argv[1])
for name in ('graph500-24', 'uniform-24', 'dota-league', 'com-lj.ungraph', 'com-orkut.ungraph', 'twitter-2010'):
    path = root / (name + '.properties')
    if not path.is_file():
        raise SystemExit(f'Missing dataset properties: {path}')
    text = path.read_text()
    prefix = f'graph.{name}.'
    for key, value in [('algorithms', 'bfs, cdlp, lcc, pr, sssp, wcc, bc'), ('bc.max-iterations', '5')]:
        pattern = rf'^{re.escape(prefix + key)}\s*=.*$'
        line = prefix + key + ' = ' + value
        text = re.sub(pattern, lambda _: line, text, flags=re.M) if re.search(pattern, text, re.M) else text + '\n' + line + '\n'
    # Absolute upstream paths would reference the publisher's machine.
    for kind in ('vertex', 'edge'):
        pattern = rf'^(graph\.[^\s]+\.{kind}-file\s*=\s*)(.+)$'
        text = re.sub(pattern, lambda m: m[1] + Path(m[2].strip()).name, text, flags=re.M)
    path.write_text(text)
    for extension in ('.e', '.v'):
        candidate = root / (name + extension)
        if not candidate.is_file() or candidate.stat().st_size == 0:
            raise SystemExit(f'Missing/empty dataset input: {candidate}')
    plain = root / (name + '.el')
    if not plain.exists():
        with (root / (name + '.e')).open() as source, plain.open('w') as target:
            for line in source:
                fields = line.split()
                if fields and not fields[0].startswith('#'):
                    target.write(' '.join(fields[:3]) + '\n')
PY

# Deduplication and property generation precede vertex workloads. The .v files
# retain isolated vertices and original IDs from the Graphalytics collection.
if [[ ! -f $DATA_DIR/.vertices-ready ]]; then
    # The upstream generator uses relative datasets/ paths. A private scratch
    # directory lets an unprivileged runtime keep the image sources read-only.
    vertex_work=$(mktemp -d)
    trap 'rm -f -- "$vertex_work/datasets"; rmdir -- "$vertex_work"' EXIT
    ln -s "$DATA_DIR" "$vertex_work/datasets"
    (cd "$vertex_work" && "$GFE_DIR/create_vertex_ops")
    for name in graph500-24 uniform-24 dota-league com-lj.ungraph com-orkut.ungraph twitter-2010; do
        [[ -s $DATA_DIR/$name.vertices.el ]] || { echo "Missing vertex workload: $name" >&2; exit 1; }
    done
    touch "$DATA_DIR/.vertices-ready"
fi

graphlog="$GFE_DIR/graphlog-base/build/graphlog"
for entry in 'graph500-24:1:graph500-24-delete' 'uniform-24:1:uniform-24-delete' \
    'graph500-24:10:graph500-24-1.0' 'uniform-24:10:uniform-24-1.0' 'dota-league:10:dota-league'; do
    IFS=: read -r graph aging output <<< "$entry"
    regenerate=0
    if [[ $PROFILE == smoke && $graph == dota-league ]]; then
        # Readers start after a one-second setup interval in the upstream
        # harness. Keep synthetic writes active long enough for real overlap.
        aging=10000
        if [[ -s $DATA_DIR/$output.graphlog ]] && ! python3 - "$DATA_DIR/$output.graphlog" <<'PY'
import re, sys
with open(sys.argv[1], 'rb') as stream:
    header = stream.read(4096).split(b'__BINARY_SECTION_FOLLOWS')[0]
raise SystemExit(0 if re.search(rb'^aging_coeff\s*=\s*10000\s*$', header, re.M) else 1)
PY
        then
            regenerate=1
        fi
    fi
    if [[ ! -s $DATA_DIR/$output.graphlog || $regenerate == 1 ]]; then
        "$graphlog" --seed 42 -a "$aging" -e 1 -v 1 "$DATA_DIR/$graph.properties" "$DATA_DIR/$output.graphlog.part"
        mv "$DATA_DIR/$output.graphlog.part" "$DATA_DIR/$output.graphlog"
    fi
done
printf 'Prepared %s inputs in %s\n' "$PROFILE" "$DATA_DIR"
