# ----------------------------
# Run RadixGraph experiments
# ----------------------------
cd /workspace/gfe_driver/build
mv gfe_driver_radixgraph gfe_driver
mv Makefile_radixgraph Makefile

# (1) Insertions, Deletions and Memory Consumptions
cd /workspace/gfe_driver
sh scripts/run_random.sh radixgraph 64
sh scripts/run_vertex.sh radixgraph 64

# (2) Mixed updates and delete only
cd /workspace/gfe_driver
sh scripts/run_mixed.sh radixgraph 64
sh scripts/run_delete_memfp.sh radixgraph 2

# (3) Analytics
cd /workspace/gfe_driver
sh scripts/run_analytics.sh radixgraph 64

# (4) Concurrent reads and writes
cd /workspace/gfe_driver
sed -i \
  -e 's/^#define[[:space:]]\+RUN_GET_NEIGHBORS[[:space:]]\+[01]/#define RUN_GET_NEIGHBORS 1/' \
  -e 's/^#define[[:space:]]\+RUN_TWO_HOP_NEIGHBORS[[:space:]]\+[01]/#define RUN_TWO_HOP_NEIGHBORS 0/' \
  configuration.hpp
cd /workspace/gfe_driver/build
make -j
cd /workspace/gfe_driver
sh scripts/run_concurrent.sh radixgraph 64 && \
  mv results/radixgraph/concurrent results/radixgraph/concurrent-1-hop
sed -i \
  -e 's/^#define[[:space:]]\+RUN_GET_NEIGHBORS[[:space:]]\+[01]/#define RUN_GET_NEIGHBORS 0/' \
  -e 's/^#define[[:space:]]\+RUN_TWO_HOP_NEIGHBORS[[:space:]]\+[01]/#define RUN_TWO_HOP_NEIGHBORS 1/' \
  configuration.hpp
cd /workspace/gfe_driver/build
cd make -j
cd /workspace/gfe_driver
sh scripts/run_concurrent.sh radixgraph 64 && \
  mv results/radixgraph/concurrent results/radixgraph/concurrent-2-hop
sed -i \
  -e 's/^#define[[:space:]]\+RUN_GET_NEIGHBORS[[:space:]]\+[01]/#define RUN_GET_NEIGHBORS 1/' \
  -e 's/^#define[[:space:]]\+RUN_TWO_HOP_NEIGHBORS[[:space:]]\+[01]/#define RUN_TWO_HOP_NEIGHBORS 1/' \
  configuration.hpp
cd /workspace/gfe_driver/build
cd make -j

# (5) Recover
cd /workspace/gfe_driver/build
mv gfe_driver gfe_driver_radixgraph && mv Makefile Makefile_radixgraph

# ----------------------------
# Run Spruce experiments
# ----------------------------
cd /workspace/gfe_driver/build
mv gfe_driver_bvgt gfe_driver
mv Makefile_bvgt Makefile

# (1) Insertions, Deletions and Memory Consumptions
cd /workspace/gfe_driver
sh scripts/run_random.sh bvgt 64
sh scripts/run_vertex.sh bvgt 64

# (2) Mixed updates and delete only
cd /workspace/gfe_driver
sh scripts/run_mixed.sh bvgt 64
sh scripts/run_delete_memfp.sh bvgt 2

# (3) Analytics
cd /workspace/gfe_driver
sh scripts/run_analytics.sh bvgt 64

# (4) Recover
cd /workspace/gfe_driver/build
mv gfe_driver gfe_driver_bvgt && mv Makefile Makefile_bvgt

# ----------------------------
# For the rest of experiments, disable twitter dataset
# ----------------------------
cd /workspace/gfe_driver
sed -i '46s/^/# /' scripts/run_random.sh
sed -i '46s/^/# /' scripts/run_vertex.sh
sed -i '46s/^/# /' scripts/run_analytics.sh

# ----------------------------
# Run Teseo experiments
# ----------------------------
cd /workspace/gfe_driver/build
mv gfe_driver_teseo gfe_driver
mv Makefile_teseo Makefile

# (1) Insertions, Deletions and Memory Consumptions
cd /workspace/gfe_driver
sh scripts/run_random.sh teseo.13 64
sh scripts/run_vertex.sh teseo.13 64

# (2) Mixed updates and delete only
cd /workspace/gfe_driver
sh scripts/run_mixed.sh teseo.13 64
sh scripts/run_delete_memfp.sh teseo.13 2

# (3) Analytics
cd /workspace/gfe_driver
sh scripts/run_analytics.sh teseo.13 64

# (4) Recover
cd /workspace/gfe_driver/build
mv gfe_driver gfe_driver_teseo && mv Makefile Makefile_teseo

# ----------------------------
# Run Sortledton experiments
# ----------------------------
cd /workspace/gfe_driver/build
mv gfe_driver_sortledton gfe_driver
mv Makefile_sortledton Makefile

# (1) Insertions, Deletions and Memory Consumptions
cd /workspace/gfe_driver
sh scripts/run_random.sh sortledton.4 64
sh scripts/run_vertex.sh sortledton.4 64

# (2) Mixed updates and delete only
cd /workspace/gfe_driver
sh scripts/run_mixed.sh sortledton.4 64
sh scripts/run_delete_memfp.sh sortledton.4 2

# (3) Analytics
cd /workspace/gfe_driver
sh scripts/run_analytics.sh sortledton.4 64

# (4) Recover
cd /workspace/gfe_driver/build
mv gfe_driver gfe_driver_sortledton && mv Makefile Makefile_sortledton

# ----------------------------
# Run GTX experiments
# ----------------------------
cd /workspace/gfe_driver/build
mv gfe_driver_gtx gfe_driver
mv Makefile_gtx Makefile
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/workspace/gfe_driver/GTX-SIGMOD2025/build

# (1) Insertions, Deletions and Memory Consumptions
cd /workspace/gfe_driver
sh scripts/run_random.sh gtx 64
sh scripts/run_vertex.sh gtx 64

# (2) Mixed updates and delete only
cd /workspace/gfe_driver
sh scripts/run_mixed.sh gtx 64
sh scripts/run_delete_memfp.sh gtx 2

# (3) Analytics
cd /workspace/gfe_driver
sh scripts/run_analytics.sh gtx 64

# (4) Concurrent reads and writes
cd /workspace/gfe_driver
sed -i \
  -e 's/^#define[[:space:]]\+RUN_GET_NEIGHBORS[[:space:]]\+[01]/#define RUN_GET_NEIGHBORS 1/' \
  -e 's/^#define[[:space:]]\+RUN_TWO_HOP_NEIGHBORS[[:space:]]\+[01]/#define RUN_TWO_HOP_NEIGHBORS 0/' \
  configuration.hpp
cd /workspace/gfe_driver/build
make -j
cd /workspace/gfe_driver
sh scripts/run_concurrent.sh gtx 64 && \
  mv results/gtx/concurrent results/gtx/concurrent-1-hop
sed -i \
  -e 's/^#define[[:space:]]\+RUN_GET_NEIGHBORS[[:space:]]\+[01]/#define RUN_GET_NEIGHBORS 0/' \
  -e 's/^#define[[:space:]]\+RUN_TWO_HOP_NEIGHBORS[[:space:]]\+[01]/#define RUN_TWO_HOP_NEIGHBORS 1/' \
  configuration.hpp
cd /workspace/gfe_driver/build
cd make -j
cd /workspace/gfe_driver
sh scripts/run_concurrent.sh gtx 64 && \
  mv results/gtx/concurrent results/gtx/concurrent-2-hop
sed -i \
  -e 's/^#define[[:space:]]\+RUN_GET_NEIGHBORS[[:space:]]\+[01]/#define RUN_GET_NEIGHBORS 1/' \
  -e 's/^#define[[:space:]]\+RUN_TWO_HOP_NEIGHBORS[[:space:]]\+[01]/#define RUN_TWO_HOP_NEIGHBORS 1/' \
  configuration.hpp
cd /workspace/gfe_driver/build
cd make -j

# (5) Recover
cd /workspace/gfe_driver/build
mv gfe_driver gfe_driver_gtx && mv Makefile Makefile_gtx

# ----------------------------
# Summarize data
# ----------------------------
cd /workspace/gfe_driver
python3 scripts/data_to_csv.py

# ----------------------------
# Plot figures
# ----------------------------
cd /workspace/gfe_driver
python3 scripts/plot_all_1.py
python3 scripts/plot_all_2.py
python3 scripts/plot_all_3.py
printf "concurrent-1-hop\nconcurrent-2-hop\n" > concurrent_results.txt
python3 scripts/plot_concurrent.py < ./concurrent_results.txt