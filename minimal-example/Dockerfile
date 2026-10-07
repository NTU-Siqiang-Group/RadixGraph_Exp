# Reproducible environment for RadixGraph experiments
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /workspace

# ----------------------------
# System dependencies
# ----------------------------
RUN apt-get update && apt-get install -y \
    gcc-11 g++-11 \
    build-essential \
    cmake \
    git \
    libnuma-dev numactl \
    libevent-dev \
    libboost-all-dev \
    zlib1g-dev \
    libsqlite3-dev \
    libpapi-dev \
    libjemalloc-dev \
    libtbb-dev \
    autoconf automake \
    python3 python3-pip python3-dev \
    wget curl ca-certificates

# Make gcc-11 / g++-11 default (matches notebook)
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-11 100 && \
    update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-11 100

# ----------------------------
# Python dependencies
# ----------------------------
RUN apt-get install -y \
    python3-tqdm \
    python3-matplotlib \
    python3-numpy \
    python3-pandas

# ----------------------------
# Clone experiment repository
# ----------------------------
RUN git clone https://github.com/ForwardStar/gfe_driver.git

# ----------------------------
# Dataset preparation
# ----------------------------
# Full dataset pipeline (very slow).
# Comment this out if you want to prepare datasets manually at runtime.
WORKDIR /workspace/gfe_driver
RUN python3 downloader.py --download-lj-only && \
    sed -i '/^#/d' datasets/*.txt && \
    sed -i -E 's/[[:space:]]+/ /g' datasets/*.txt && \
    mv datasets/com-lj.ungraph.txt datasets/com-lj.ungraph.el

# Create vertex ops (as in notebook)
WORKDIR /workspace/gfe_driver
RUN g++ scripts/create_vertex_ops.cpp -o create_vertex_ops -O3 && \
    ./create_vertex_ops

# Remove duplicate edges (as in notebook)
WORKDIR /workspace/gfe_driver
RUN g++ scripts/remove_duplicate_edges.cpp -O3 -o remove_duplicate_edges && \
    ./remove_duplicate_edges ./datasets/com-lj.ungraph.el

# ----------------------------
# Generate property files
# ----------------------------
WORKDIR /workspace/gfe_driver
RUN g++ scripts/generate_property_files.cpp -o generate_property_files -O3 && \
    ./generate_property_files datasets/com-lj.ungraph.el && \
    ls -lah datasets

# ----------------------------
# Generate graphlog
# ----------------------------
WORKDIR /workspace/gfe_driver
RUN git clone https://github.com/MordorsElite/graphlog-base.git && \
    cd graphlog-base && \
    git submodule update --init && \
    mkdir build && \
    cd build && \
    cmake ../ -DCMAKE_BUILD_TYPE=Release && \
    make -j
WORKDIR /workspace/gfe_driver/graphlog-base/build

# ----------------------------
# Configure GFE Driver
# ----------------------------
WORKDIR /workspace/gfe_driver
RUN git submodule update --init && \
    mkdir build && \
    cd build && \
    autoreconf -iv ..

# ----------------------------
# Build GFE Driver for RadixGraph
# ----------------------------
WORKDIR /workspace/gfe_driver/library/radixgraph/RadixGraph
RUN git submodule update --init --recursive && \
    cmake -S . -DCMAKE_BUILD_TYPE=Release && \
    make -j && \
    g++ optimizer.cpp -o optimizer -O3 && \
    mv optimizer ../../../ && \
    cd ../../../build && \
    ../configure --enable-optimize --disable-debug --enable-mem-analysis --with-radixgraph=../library/radixgraph/RadixGraph && \
    make clean && make -j && \
    mv gfe_driver gfe_driver_radixgraph && \
    mv Makefile Makefile_radixgraph

# ----------------------------
# Build GFE Driver for Spruce
# ----------------------------
# Firstly install junction
WORKDIR /workspace/gfe_driver
RUN git clone https://github.com/preshing/junction.git && \
    git clone https://github.com/preshing/turf.git && \
    cd junction && \
    mkdir build && \
    cd build && \
    cmake -DJUNCTION_WITH_SAMPLES=OFF .. && \
    make install
# Then build spruce
WORKDIR /workspace/gfe_driver
RUN wget -c https://github.com/Stardust-SJF/gfe_driver/releases/download/v2.0.0/libBVGT_stable.a && \
    mv libBVGT_stable.a libBVGT.a
RUN cd build && \
    ../configure --enable-optimize --enable-mem-analysis --disable-debug --with-bvgt=../ && \
    make clean && make -j && \
    mv gfe_driver gfe_driver_bvgt && \
    mv Makefile Makefile_bvgt

# ----------------------------
# Build GFE Driver for Teseo
# ----------------------------
WORKDIR /workspace/gfe_driver
ARG TESEO_REV=2c37c2831c4d2acaaa838a86e1318363ce68c45b
RUN git clone https://github.com/cwida/teseo.git && \
    git -C teseo checkout --detach "${TESEO_REV}"

# Teseo's non-NUMA mode works with Docker's default syscall restrictions.
# Keep its thread contexts on the single allocated node, including on NUMA hosts.
RUN python3 - <<'PYFIX'
from pathlib import Path

path = Path('/workspace/gfe_driver/teseo/src/context/thread_context.cpp')
text = path.read_text()
old = 'm_cache_numa_thread = util::Thread::get_numa_id();'
if text.count(old) != 1:
    raise SystemExit('Teseo NUMA thread assignment changed; inspect non-NUMA allocation bounds')
header = '#include "teseo/context/static_configuration.hpp"'
if header not in text:
    text = header + '\n' + text
text = text.replace(old, 'm_cache_numa_thread = StaticConfiguration::numa_enabled ? util::Thread::get_numa_id() : 0;')
path.write_text(text)
PYFIX

RUN cd teseo && \
    autoreconf -iv && \
    mkdir build && cd build && \
    ../configure --enable-optimize --disable-debug --disable-numa && \
    make -j

RUN cd build && \
    ../configure --enable-optimize --disable-debug --enable-mem-analysis --with-teseo=../teseo/build && \
    make clean && make -j && \
    mv gfe_driver gfe_driver_teseo && \
    mv Makefile Makefile_teseo

# ----------------------------
# Build GFE Driver for Sortledton
# ----------------------------
WORKDIR /workspace/gfe_driver
RUN git clone https://gitlab.db.in.tum.de/per.fuchs/sortledton && \
    cd sortledton && \
    git reset --hard && \
    sed -i '11d' CMakeLists.txt && \
    mkdir build && \
    cd build && \
    cmake -DCMAKE_BUILD_TYPE=Release .. && \
    make sortledton && \
    cd ../../build && \
    ../configure --enable-optimize --disable-debug --enable-mem-analysis --with-sortledton=../sortledton/build/ && \
    make clean && make -j && \
    mv gfe_driver gfe_driver_sortledton && \
    mv Makefile Makefile_sortledton

# ----------------------------
# Build GFE Driver for GTX
# ----------------------------
WORKDIR /workspace/gfe_driver
RUN git clone https://github.com/Jiboxiake/GTX-SIGMOD2025 && \
  cd GTX-SIGMOD2025 && \
  git submodule update --init --recursive && \
  mkdir build && \
  cd build && \
  cmake -DCMAKE_BUILD_TYPE=Release .. && \
  make -j && \
  cd ../../build && \
  ../configure --enable-optimize --disable-debug --enable-mem-analysis --with-gtx=../GTX-SIGMOD2025/build && \
  export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:../GTX-SIGMOD2025/build && \
  make clean && make -j && \
  mv gfe_driver gfe_driver_gtx && \
  mv Makefile Makefile_gtx

# ----------------------------
# Default shell
# ----------------------------
WORKDIR /workspace/gfe_driver
CMD ["/bin/bash"]
