#include "common.hpp"
#include "graph/api.h"

int main(int argc, char** argv) {
  try {
    if (argc < 2) throw std::runtime_error("Usage: batch_benchmark EDGE_FILE [TRIALS]");
    auto input = batch_benchmark::load(argv[1]);
    const auto before = batch_benchmark::rss_bytes();
    // Aspen takes ownership and calls pbbs::free_array on its CSR arguments.
    auto offsets = pbbs::new_array_no_init<uintE>(input.offsets.size());
    auto neighbors = pbbs::new_array_no_init<uintV>(input.neighbors.size());
    std::copy(input.offsets.begin(), input.offsets.end(), offsets);
    std::copy(input.neighbors.begin(), input.neighbors.end(), neighbors);
    auto graph = versioned_graph<treeplus_graph>(input.n, input.neighbors.size(), offsets, neighbors);
    batch_benchmark::memory(before);
    std::cout << "BATCH_THREADS " << num_workers() << std::endl;
    batch_benchmark::run(input, argc > 2 ? std::stoul(argv[2]) : 20,
      [&](std::vector<batch_benchmark::Edge>& edges) {
        graph.insert_edges_batch(edges.size(), edges.data(), false, true, input.n, false);
      },
      [&](std::vector<batch_benchmark::Edge>& edges) {
        graph.delete_edges_batch(edges.size(), edges.data(), false, true, input.n, false);
      });
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << std::endl;
    return 1;
  }
}
