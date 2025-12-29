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

# (2) Analytics
cd /workspace/gfe_driver
sh scripts/run_analytics.sh radixgraph 64

# (3) Recover
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

# (2) Analytics
cd /workspace/gfe_driver
sh scripts/run_analytics.sh bvgt 64

# (3) Recover
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
if [ -e "gfe_driver_teseo" ]; then
    mv gfe_driver_teseo gfe_driver
    mv Makefile_teseo Makefile

    # (1) Insertions, Deletions and Memory Consumptions
    cd /workspace/gfe_driver
    sh scripts/run_random.sh teseo.13 64
    sh scripts/run_vertex.sh teseo.13 64

    # (2) Analytics
    cd /workspace/gfe_driver
    sh scripts/run_analytics.sh teseo.13 64

    # (3) Recover
    cd /workspace/gfe_driver/build
    mv gfe_driver gfe_driver_teseo && mv Makefile Makefile_teseo
else
    echo "Teseo is not built. Skipped"
fi

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

# (2) Analytics
cd /workspace/gfe_driver
sh scripts/run_analytics.sh sortledton.4 64

# (3) Recover
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

# (2) Analytics
cd /workspace/gfe_driver
sh scripts/run_analytics.sh gtx 64

# (3) Recover
cd /workspace/gfe_driver/build
mv gfe_driver gfe_driver_gtx && mv Makefile Makefile_gtx