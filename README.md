# RadixGraph_Exp

This is the repository for reproducing experimental results in the paper: [RadixGraph: A Fast, Space-Optimized Data Structure for Dynamic Graph Storage](https://arxiv.org/pdf/2601.01444) (accepted by SIGMOD 2026).

RadixGraph repo: [https://github.com/ForwardStar/RadixGraph](https://github.com/ForwardStar/RadixGraph)

GFE driver for RadixGraph (test kits): [https://github.com/ForwardStar/gfe_driver](https://github.com/ForwardStar/gfe_driver)

## Minimum requirements
To reproduce the full experiments, following are required:

- CPU: at least supporting 64 threads;

- Memory: >= 256G;

- Disk: >= 512G.

## Reproduce RadixGraph with Docker

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

Find the generated figures in `output/<UTC-run-id>/figures/` and tables in `output/<UTC-run-id>/tables/`.

### Run the experiments stage by stage

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

# 2. Run the GFE benchmarks for Figures 8–11.
run_radixgraph --stages main --skip-prepare

# 3. Run case studies and batch updates for Figures 12–13 and Tables 5–7.
run_radixgraph --stages studies,batch --skip-prepare
```

Each invocation creates a new `output/<UTC-run-id>/` and prints its path. All stages reuse the same `data/` cache, and figures and tables are exported automatically after each invocation.

To regenerate figures and tables from an existing run without rerunning experiments, replace `<UTC-run-id>` with that run's directory name:

```sh
run_radixgraph --stages report --run-dir "/output/<UTC-run-id>"
```

Existing run directories can be reused only for `report`; experiment stages always write to a new directory.

### Estimated runtime

Allow approximately **3–7 days for the first full run** on a 64-thread Xeon server meeting the memory and disk requirements above.

| Stage | Estimated time |
| --- | --- |
| Docker build, if needed | 30–90 minutes |
| Download, preprocessing and graphlog generation | 4–24 hours |
| All experiments | 2–6 days |
| Plotting and table export | A few minutes |

### Bypass requirement checks

To bypass CPU, RAM or disk requirements, add `--force`:

```sh
run_radixgraph --stages all --force
```

For the full `docker run` command, append `--force` after `--stages all`.

We recommend meeting the requirements; forced runs may fail or produce incomplete results.

## Reproduce RadixGraph with Jupyter Notebook in a step-by-step manner

You can also reproduce the experiments with [``reproduce_radixgraph.ipynb``](https://github.com/NTU-Siqiang-Group/RadixGraph_Exp/blob/main/reproduce_radixgraph.ipynb) in a Jupyter Notebook, which gives more detailed instructions and explanations.

It also provides a minimal example of testing on LiveJournal dataset.

## Reproduce RadixGraph manually

Refer to READMEs in [GFE Driver for RadixGraph](https://github.com/ForwardStar/gfe_driver) and [RadixGraph](https://github.com/ForwardStar/RadixGraph) to manually reproduce the experimental results.
