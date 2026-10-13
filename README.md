# RadixGraph_Exp

This is the repository for reproducing experimental results in the paper: [RadixGraph: A Fast, Space-Optimized Data Structure for Dynamic Graph Storage](https://arxiv.org/pdf/2601.01444) (accepted by SIGMOD 2026).

RadixGraph repo: [https://github.com/ForwardStar/RadixGraph](https://github.com/ForwardStar/RadixGraph)

GFE driver for RadixGraph (test kits): [https://github.com/ForwardStar/gfe_driver](https://github.com/ForwardStar/gfe_driver)

## Minimum requirements

### Platform and software

- Supported platform: **Linux on x86-64** (`x64` / `amd64`). Both Docker images use Ubuntu 24.04.
- Software: a working Docker installation capable of building and running Linux x86-64 containers, and Bash for the commands below.
- Network access: required to download dependencies and source repositories during the image build, and datasets during preparation.

**ARM/ARM64 is currently unsupported, including Apple Silicon (M-series Macs).** We try our best to support ARM. However, the Spruce baseline experiment depends on the released, precompiled [`libBVGT_stable.a` library](https://github.com/Stardust-SJF/gfe_driver/releases/tag/v2.0.0), which was built for x86-64. The build downloads this archive as `libBVGT.a`; it cannot be linked into a native ARM64 executable.

### Hardware for full experiments

To reproduce the full paper experiments, the following resources must be available to the container:

- CPU: at least 64 usable logical CPU threads.
- Memory: at least 256 GB of accessible RAM, swap is excluded.
- Storage: at least 512 GB for the dataset cache. Allow additional space for Docker images, build caches, and experiment outputs, and ensure the dataset and output directories are writable.

## Reproduce RadixGraph with Docker

[![docker-livejournal-test](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/docker-livejournal-test.yml/badge.svg?branch=main)](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/docker-livejournal-test.yml)
[![docker-smoke-test](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/docker-smoke-test.yml/badge.svg?branch=main)](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/docker-smoke-test.yml)

Run the following commands from the repository root.

Build the image:
```sh
docker build --build-arg BUILD_JOBS=2 -t radixgraph-exp .
```

Create directories for the dataset cache and results:
```sh
mkdir -p data output
```

### Run full experiments

Run all paper experiments and generate the figures and tables:
```sh
docker_user="$(id -u):$(id -g)"
if docker info --format '{{json .SecurityOptions}}' | grep -q rootless; then
  docker_user=0:0
fi

docker run --rm --init --stop-timeout 60 \
  --user "$docker_user" \
  --mount type=bind,source="$PWD/data",target=/data \
  --mount type=bind,source="$PWD/output",target=/output \
  radixgraph-exp bash /opt/radixgraph-exp/run.sh --profile paper --stages all
```

Find plots and table PDFs are in `output/<UTC-run-id>/figures/`, and raw CSV results are in `output/<UTC-run-id>/tables/`.

### Run the experiments stage by stage (Recommended)

Alternatively, after building the image and creating `data/` and `output/`, define this helper in Bash from the repository root:

```sh
run_radixgraph() {
  local docker_user="$(id -u):$(id -g)"
  if docker info --format '{{json .SecurityOptions}}' | grep -q rootless; then
    docker_user=0:0
  fi
  docker run --rm --init --stop-timeout 60 \
    --user "$docker_user" \
    --mount type=bind,source="$PWD/data",target=/data \
    --mount type=bind,source="$PWD/output",target=/output \
    radixgraph-exp bash /opt/radixgraph-exp/run.sh --profile paper "$@"
}
```

Check the machine without downloading data or running experiments:

```sh
run_radixgraph --check-requirements
```

Run these stages in order:

```sh
# 1. Download datasets, preprocess inputs and generate graphlogs.
run_radixgraph --stages prepare

# 2. Run Figure 8.
run_radixgraph --stages figure8 --skip-prepare

# 3. Run Figure 9.
run_radixgraph --stages figure9 --skip-prepare

# 4. Run Figure 10.
run_radixgraph --stages figure10 --skip-prepare

# 5. Run Figure 11.
run_radixgraph --stages figure11 --skip-prepare

# 6. Run Figure 12 (all panels).
run_radixgraph --stages figure12 --skip-prepare

# 7. Run Figure 13.
run_radixgraph --stages figure13 --skip-prepare

# 8. Run Tables 5–7, including batch-update baselines.
run_radixgraph --stages tables,batch --skip-prepare
```

Multiple stages can be selected with commas.

Each invocation creates a new `output/<UTC-run-id>/` and prints its path, and resulting figures are stored in `output/<UTC-run-id>/figures`. Note that Figure 12(d) is exported as `tables/figure12_transformations.csv`.

To regenerate figures and tables from an existing run without rerunning experiments, replace `<UTC-run-id>` with that run's directory name:

```sh
run_radixgraph --stages report --run-dir "/output/<UTC-run-id>"
```

Existing run directories can be reused only for `report`; experiment stages always write to a new directory.

### Estimated runtime

Allow **several days for the first full run** on a 64-thread Xeon server meeting the memory and disk requirements above.

| Stage | Estimated time |
| --- | --- |
| Docker build, if needed | 30–90 minutes |
| 1. `prepare`: download datasets and preprocessing | About 12 hours |
| 2. `figure8`: Figure 8 | About 3 hours |
| 3. `figure9`: Figure 9 | About 4 hours |
| 4. `figure10`: Figure 10 | About **5 days** |
| 5. `figure11`: Figure 11 | About 2 hours |
| 6. `figure12`: Figure 12, all panels | A few minutes |
| 7. `figure13`: Figure 13 | A few minutes |
| 8. `tables,batch`: Tables 5–7 and batch-update baselines | About 24 hours |
| `report`: regenerate plots and tables from an existing run | A few minutes |

### Bypass requirement checks

To bypass CPU, RAM or disk requirements, add `--force`:

```sh
run_radixgraph --stages all --force
```

For the full `docker run` command, append `--force` after `--stages all`.

We recommend meeting the requirements; forced runs may fail or produce incomplete results. `--force` only bypasses resource checks; it does not enable ARM support.

### Acknowledgement

The Docker reproduction pipeline was largely developed and validated with assistance from [OpenAI Codex](https://openai.com/codex/).

## Reproduce RadixGraph with Jupyter Notebook in a step-by-step manner

You can also reproduce the experiments with [``reproduce_radixgraph.ipynb``](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/blob/main/reproduce_radixgraph.ipynb) in a Jupyter Notebook, which gives more detailed instructions and explanations.

It also provides a minimal example of testing on LiveJournal dataset.

## Reproduce RadixGraph manually

Refer to READMEs in [GFE Driver for RadixGraph](https://github.com/ForwardStar/gfe_driver) and [RadixGraph](https://github.com/ForwardStar/RadixGraph) to manually reproduce the experimental results.
