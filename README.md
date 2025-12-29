# RadixGraph_Exp

This is the repository for reproducing experimental results in the paper: [RadixGraph: A Fast, Space-Optimized Data Structure for Dynamic Graph Storage]() (accpeted by SIGMOD 2026).

RadixGraph repo: [https://github.com/ForwardStar/RadixGraph](https://github.com/ForwardStar/RadixGraph)

GFE driver for RadixGraph (test kits): [https://github.com/ForwardStar/gfe_driver](https://github.com/ForwardStar/gfe_driver)

## Reproduce RadixGraph with Docker

There are two images you can build: ``radixgraph:minimal`` and ``radixgraph:full``. The ``minimal`` image only tests the LiveJournal dataset experiments, while the ``full`` image contains all datasets.

Build:
```bash
docker build -f Dockerfile_minimal -t radixgraph:minimal .
docker build -f Dockerfile_full -t radixgraph:full .
```

Run whichever image you want:
```bash
docker run -it radixgraph:minimal
docker run -it radixgraph:full
```

## Reproduce RadixGraph with Jupyter Notebook in a step-by-step manner

You can also reproduce the experiments with ``reproduce_radixgraph.ipynb`` in a Jupyter Notebook, which gives more detailed instructions and explanations.

## Reproduce RadixGraph manually

Refer to READMEs in [GFE Driver for RadixGraph](https://github.com/ForwardStar/gfe_driver) and [RadixGraph](https://github.com/ForwardStar/RadixGraph) to manually reproduce the experimental results.