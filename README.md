# RadixGraph_Exp

This is the repository for reproducing experimental results in the paper: [RadixGraph: A Fast, Space-Optimized Data Structure for Dynamic Graph Storage](https://arxiv.org/pdf/2601.01444) (accepted by SIGMOD 2026).

RadixGraph repo: [https://github.com/ForwardStar/RadixGraph](https://github.com/ForwardStar/RadixGraph)

GFE driver for RadixGraph (test kits): [https://github.com/ForwardStar/gfe_driver](https://github.com/ForwardStar/gfe_driver)

## Minimum requirements

### Platform and software

- Platforms for the main Docker image: **Linux on x86-64** (`x64` / `amd64`) and **Linux on ARM64** (`arm64` / `aarch64`). The image uses Ubuntu 24.04. 32-bit ARM is unsupported.
- Software: a working Docker installation capable of building and running Linux containers for your target architecture, and Bash for the commands below.
- Network access: required to download dependencies and source repositories during the image build, and datasets during preparation.

**macOS:** The intended setup is [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/), which runs the Linux containers inside a Linux VM. With a local Docker Desktop builder, Apple Silicon (M-series) Macs use the ARM64 path by default, and Intel Macs use the x86-64 path.

**We strongly recommend running on Linux on x86-64. ARM64 runs only RadixGraph, Sortledton, and GTX, while Spruce (`bvgt`) and Teseo are excluded.** Spruce's released, precompiled [`libBVGT_stable.a` library](https://github.com/Stardust-SJF/gfe_driver/releases/tag/v2.0.0) targets x86-64, so ARM64 builds skip downloading and linking it. Teseo's CPU-affinity topology detection assumes x86-style `/proc/cpuinfo` fields and fails on ARM64; its library and driver builds are skipped. Experiments automatically select the three supported systems, and explicitly selecting an excluded system reports an error. The x86-64 image retains all five systems.

### Hardware for full experiments

- CPU: at least 64 usable logical CPU threads.
- Memory: at least 256 GB of accessible RAM, swap is excluded.
- Storage: at least 512 GB for the dataset cache. Allow additional space for Docker images, build caches, and experiment outputs, and ensure the dataset and output directories are writable.

On macOS, Docker Desktop's Linux VM must have sufficient CPU and RAM allocated, and the mounted dataset cache must have sufficient storage. The Mac's total physical resources alone do not establish what the container can use.

## Reproduce RadixGraph with Docker

[![livejournal-test-x64](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/livejournal-test-x64.yml/badge.svg?branch=main)](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/livejournal-test-x64.yml)
[![livejournal-test-arm64](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/livejournal-test-arm64.yml/badge.svg?branch=main)](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/livejournal-test-arm64.yml)
[![smoke-test-x64](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/smoke-test-x64.yml/badge.svg?branch=main)](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/smoke-test-x64.yml)
[![smoke-test-arm64](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/smoke-test-arm64.yml/badge.svg?branch=main)](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/actions/workflows/smoke-test-arm64.yml)

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

Run the paper profile and generate the figures and tables for the image's available systems. On ARM64, Spruce and Teseo are excluded:
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

Find plots and table PDFs are in `output/<UTC-run-id>/figures/`, and raw CSV results are in `output/<UTC-run-id>/tables/`. The estimated runtime of full experiments is **7 days**.

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
```

Multiple stages can be selected with commas.

Each invocation creates a new `output/<UTC-run-id>/` and prints its path, and resulting figures are stored in `output/<UTC-run-id>/figures`. Note that Figure 12(d) is exported as `tables/figure12_transformations.csv`.

To regenerate figure reports from an existing run without rerunning experiments, replace `<UTC-run-id>` with that run's directory name:

```sh
run_radixgraph --report --run-dir "/output/<UTC-run-id>"
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
| 4. `figure10`: Figure 10 | About **6 days** |
| 5. `figure11`: Figure 11 | About 2 hours |
| 6. `figure12`: Figure 12, all panels | A few minutes |
| 7. `figure13`: Figure 13 | A few minutes |
| `report`: regenerate figure reports from an existing run | A few minutes |

### Bypass requirement checks

To bypass CPU, RAM or disk requirements, add `--force`:

```sh
run_radixgraph --stages all --force
```

For the full `docker run` command, append `--force` after `--stages all`.

We recommend meeting the requirements; forced runs may fail or produce incomplete results. `--force` only bypasses resource checks; ARM64 builds always exclude Spruce and Teseo.

### Acknowledgement

The Docker reproduction pipeline was largely developed and validated with assistance from [OpenAI Codex](https://openai.com/codex/). For details, refer to the [AI collaboration report](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/blob/main/AI_collaboration_report.md).

## Reproduce RadixGraph with Jupyter Notebook in a step-by-step manner

You can also reproduce the experiments with [``reproduce_radixgraph.ipynb``](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/blob/main/reproduce_radixgraph.ipynb) in a Jupyter Notebook, which gives more detailed instructions and explanations.

It also provides a minimal example of testing on LiveJournal dataset.

## Reproduce RadixGraph manually

Refer to READMEs in [GFE Driver for RadixGraph](https://github.com/ForwardStar/gfe_driver) and [RadixGraph](https://github.com/ForwardStar/RadixGraph) to manually reproduce the experimental results.
