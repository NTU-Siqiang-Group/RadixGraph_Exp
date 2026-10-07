#include "common.hpp"
#include <parlay/parallel.h>
#include "aspen/aspen.h"

int main(int argc, char** argv) {
  try {
    if (argc < 2) throw std::runtime_error("Usage: batch_benchmark EDGE_FILE [TRIALS]");
    auto input = batch_benchmark::load(argv[1]);
    std::vector<aspen::uintT> offsets(input.offsets.begin(), input.offsets.end());
    std::vector<aspen::uintE> neighbors(input.neighbors.begin(), input.neighbors.end());
    auto parsed = std::make_tuple(input.n, neighbors.size(), offsets.data(), neighbors.data());
    const auto before = batch_benchmark::rss_bytes();
    auto graph = aspen::symmetric_graph_from_static_graph(parsed);
    batch_benchmark::memory(before);
    std::cout << "BATCH_THREADS " << parlay::num_workers() << std::endl;
    batch_benchmark::run(input, argc > 2 ? std::stoul(argv[2]) : 20,
      [&](std::vector<batch_benchmark::Edge>& edges) {
        graph.insert_edges_batch_inplace(edges.size(), edges.data(), false, true, input.n, false);
      },
      [&](std::vector<batch_benchmark::Edge>& edges) {
        graph.delete_edges_batch_inplace(edges.size(), edges.data(), false, true, input.n, false);
      });
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << std::endl;
    return 1;
  }
}
