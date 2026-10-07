#define ENABLE_LOCK 1
#define WEIGHTED 0
#define VERIFY 0
#include "common.hpp"
#include "graph.h"
#include "util.h"

int main(int argc, char** argv) {
  try {
    if (argc < 2) throw std::runtime_error("Usage: batch_benchmark EDGE_FILE [TRIALS]");
    auto input = batch_benchmark::load(argv[1]);
    std::vector<uint32_t> sources(input.neighbors.size());
    for (size_t vertex = 0; vertex < input.n; ++vertex)
      for (size_t i = input.offsets[vertex]; i < input.offsets[vertex + 1]; ++i) sources[i] = vertex;
    if (sources.size() > std::numeric_limits<uint32_t>::max())
      throw std::runtime_error("Terrace batch API supports at most UINT32_MAX initial directed edges");
    auto permutation = batch_benchmark::permutation(sources.size());
    const auto before = batch_benchmark::rss_bytes();
    graphstore::Graph graph(input.n);
    graph.add_edge_batch(sources.data(), input.neighbors.data(), sources.size(), permutation);
    batch_benchmark::memory(before);
    std::cout << "BATCH_THREADS " << getWorkers() << std::endl;
    batch_benchmark::run(input, argc > 2 ? std::stoul(argv[2]) : 20,
      [&](std::vector<batch_benchmark::Edge>& edges) {
        // Terrace requires source-sorted batches; include its preparation in the timing.
        std::sort(edges.begin(), edges.end());
        edges.erase(std::unique(edges.begin(), edges.end()), edges.end());
        std::vector<uint32_t> src(edges.size()), dst(edges.size());
        for (size_t i = 0; i < edges.size(); ++i) {
          src[i] = std::get<0>(edges[i]); dst[i] = std::get<1>(edges[i]);
        }
        auto perm = batch_benchmark::permutation(edges.size());
        graph.add_edge_batch(src.data(), dst.data(), edges.size(), perm);
      },
      [&](std::vector<batch_benchmark::Edge>& edges) {
        parallel_for (size_t i = 0; i < edges.size(); ++i)
          graph.remove_edge(std::get<0>(edges[i]), std::get<1>(edges[i]));
      });
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << std::endl;
    return 1;
  }
}
