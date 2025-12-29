# RadixGraph_Exp

This is the repository for reproducing experimental results in the paper: [RadixGraph: A Fast, Space-Optimized Data Structure for Dynamic Graph Storage]() (accepted by SIGMOD 2026).

RadixGraph repo: [https://github.com/ForwardStar/RadixGraph](https://github.com/ForwardStar/RadixGraph)

GFE driver for RadixGraph (test kits): [https://github.com/ForwardStar/gfe_driver](https://github.com/ForwardStar/gfe_driver)

## Minimum requirements
To reproduce the full experiments, following are required:

- CPU: at least supporting 64 threads;

- Memory: >= 256G;

- Disk: >= 512G.

## Reproduce RadixGraph with Docker

Firstly build the image:
```sh
docker build -t radixgraph-exp .
```

Run full experiments:
```sh
docker run radixgraph-exp -v ./:/workspace/exp radixgraph-exp bash /workspace/exp/run.sh
```

## Reproduce RadixGraph with Jupyter Notebook in a step-by-step manner

You can also reproduce the experiments with ``reproduce_radixgraph.ipynb`` in a Jupyter Notebook, which gives more detailed instructions and explanations.

It also provides a minimal example of testing on LiveJournal dataset.

## Reproduce RadixGraph manually

Refer to READMEs in [GFE Driver for RadixGraph](https://github.com/ForwardStar/gfe_driver) and [RadixGraph](https://github.com/ForwardStar/RadixGraph) to manually reproduce the experimental results.