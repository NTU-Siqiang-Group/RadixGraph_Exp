# RadixGraph_Docker

Reproduce RadixGraph experimental results using Docker.

RadixGraph repo: [https://github.com/ForwardStar](https://github.com/ForwardStar)

GFE driver for RadixGraph (test kits): [https://github.com/ForwardStar/gfe_driver](https://github.com/ForwardStar/gfe_driver)

# Build the minimal Docker image

This image only tests the LiveJournal dataset experiments.

```bash
docker build -f Dockerfile_minimal -t radixgraph:minimal .
```

# Build the full Docker image

This image includes all datasets and experiments. It might take days to finish.

```bash
docker build -f Dockerfile_full -t radixgraph:full .
```

# Run whichever image you want
```bash
docker run -it radixgraph:minimal
```

or:
```bash
docker run -it radixgraph:full
```