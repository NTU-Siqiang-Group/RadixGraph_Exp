# Build once; keep downloaded data and experimental results in host mounts.
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=60 update && \
    apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=60 install -y --no-install-recommends \
    gcc-11 g++-11 build-essential cmake git autoconf automake libtool pkg-config \
    libnuma-dev numactl libevent-dev libboost-dev libboost-container-dev \
    libboost-stacktrace-dev libboost-program-options-dev libboost-filesystem-dev \
    libboost-system-dev zlib1g-dev libsqlite3-dev \
    libpapi-dev libjemalloc-dev libtbb-dev libssl-dev \
    python3 python3-dev python3-tqdm python3-matplotlib python3-numpy python3-pandas \
    wget curl ca-certificates file time && \
    update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-11 100 && \
    update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-11 100 && \
    rm -rf /var/lib/apt/lists/*

# Source selections follow the dependency layer so pin changes reuse packages.
ARG BUILD_JOBS=2
ARG GFE_REV=81f31ceb8807078dcc3766c0896b053544d6eb6d
ARG RADIX_REV=9feba538841c000364b07117c2494646ab0b0740
ARG GRAPHLOG_REV=25d7d91fee9841cad5c7d864c7678225f5b5c790
ARG UNODB_REV=89f52799743ec2093426bdcf7a7cbaaa95ca848c
ARG JUNCTION_REV=fa76568b3bf6665965a8281d1765db8608633ee8
ARG TURF_REV=29ba08510207cb1ecf4c533a4ca60a60712600ce
ARG TESEO_REV=2c37c2831c4d2acaaa838a86e1318363ce68c45b
ARG SORTLEDTON_REV=6eb638f3ad38f8a10a127e7e118528f4c8d07a6e
ARG GTX_REV=92b292c002d9f680690e4fee35fb13323515a6b9


ENV CC=gcc-11 CXX=g++-11 MPLBACKEND=Agg PYTHONUNBUFFERED=1 \
    GFE_DIR=/workspace/gfe_driver RADIX_DIR=/workspace/RadixGraph \
    DATA_DIR=/data OUTPUT_DIR=/output \
    LD_LIBRARY_PATH=/workspace/gfe_driver/GTX-SIGMOD2025/build:/usr/local/lib
WORKDIR /workspace

# Cache fetches and independent compiler stages separately. Dataset downloads
# occur only when the runtime preparation stage is requested.
COPY scripts/build/fetch.sh /opt/radixgraph-exp/scripts/build/fetch.sh
RUN BUILD_JOBS=${BUILD_JOBS} GFE_REV=${GFE_REV} RADIX_REV=${RADIX_REV} \
    GRAPHLOG_REV=${GRAPHLOG_REV} UNODB_REV=${UNODB_REV} \
    JUNCTION_REV=${JUNCTION_REV} TURF_REV=${TURF_REV} TESEO_REV=${TESEO_REV} \
    SORTLEDTON_REV=${SORTLEDTON_REV} GTX_REV=${GTX_REV} \
    bash /opt/radixgraph-exp/scripts/build/fetch.sh

COPY scripts/build/patch-atomics.py /opt/radixgraph-exp/scripts/build/patch-atomics.py
COPY scripts/build/standalone.sh /opt/radixgraph-exp/scripts/build/standalone.sh
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/standalone.sh sort
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/standalone.sh art
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/standalone.sh sort-no-chain
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/standalone.sh index

COPY scripts/build/radix-lib.sh /opt/radixgraph-exp/scripts/build/radix-lib.sh
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/radix-lib.sh

COPY scripts/build/graphlog.sh /opt/radixgraph-exp/scripts/build/graphlog.sh
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/graphlog.sh

COPY scripts/build/junction.sh /opt/radixgraph-exp/scripts/build/junction.sh
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/junction.sh

COPY scripts/build/teseo.sh /opt/radixgraph-exp/scripts/build/teseo.sh
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/teseo.sh

COPY scripts/build/sortledton.sh /opt/radixgraph-exp/scripts/build/sortledton.sh
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/sortledton.sh

COPY scripts/build/gtx.sh /opt/radixgraph-exp/scripts/build/gtx.sh
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/gtx.sh

COPY scripts/build/gfe-autoconf.sh /opt/radixgraph-exp/scripts/build/gfe-autoconf.sh
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/gfe-autoconf.sh

COPY scripts/build/gfe-driver.sh /opt/radixgraph-exp/scripts/build/gfe-driver.sh
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/gfe-driver.sh radixgraph
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/gfe-driver.sh radixgraph_1hop
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/gfe-driver.sh radixgraph_2hop
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/gfe-driver.sh bvgt
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/gfe-driver.sh teseo
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/gfe-driver.sh sortledton
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/gfe-driver.sh gtx
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/gfe-driver.sh gtx_1hop
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build/gfe-driver.sh gtx_2hop
COPY scripts/build/manifest.sh /opt/radixgraph-exp/scripts/build/manifest.sh
RUN bash /opt/radixgraph-exp/scripts/build/manifest.sh

COPY scripts/build-batch-baselines.sh /opt/radixgraph-exp/scripts/build-batch-baselines.sh
COPY scripts/batch/ /opt/radixgraph-exp/scripts/batch/
RUN BUILD_JOBS=${BUILD_JOBS} bash /opt/radixgraph-exp/scripts/build-batch-baselines.sh

COPY run.sh /opt/radixgraph-exp/run.sh
COPY scripts/ /opt/radixgraph-exp/scripts/
RUN mkdir -p /data /output && chmod +x /opt/radixgraph-exp/run.sh \
    /opt/radixgraph-exp/scripts/*.sh
ENV MPLCONFIGDIR=/tmp/matplotlib
RUN git config --system --add safe.directory /workspace/gfe_driver && \
    git config --system --add safe.directory /workspace/gfe_driver/graphlog-base
WORKDIR /opt/radixgraph-exp
CMD ["bash", "/opt/radixgraph-exp/run.sh"]
