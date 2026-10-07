#pragma once

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

// Table 7 starts with an undirected, unweighted graph. Keep the CSR input
// resident across the memory samples, as RadixGraph keeps its input edges.
namespace batch_benchmark {
using Edge = std::tuple<uint32_t, uint32_t>;
struct CSR {
  size_t n;
  std::vector<uint64_t> offsets;
  std::vector<uint32_t> neighbors;
  std::vector<uint32_t> vertices;
};

inline CSR load(const char* filename) {
  std::ifstream input(filename);
  if (!input) throw std::runtime_error("Cannot open edge file");
  std::vector<Edge> edges;
  uint64_t largest = 0;
  std::string line;
  size_t line_number = 0;
  while (std::getline(input, line)) {
    ++line_number;
    auto first = line.find_first_not_of(" \t\r");
    if (first == std::string::npos || line[first] == '#' || line[first] == '%') continue;
    const char* current = line.c_str() + first;
    if (*current == '-') throw std::runtime_error("Negative vertex ID");
    char* next;
    uint64_t source = std::strtoull(current, &next, 10);
    if (next == current) throw std::runtime_error("Invalid source at line " + std::to_string(line_number));
    current = next;
    while (*current == ' ' || *current == '\t') ++current;
    if (*current == '-') throw std::runtime_error("Negative vertex ID");
    uint64_t dest = std::strtoull(current, &next, 10);
    if (next == current || source >= std::numeric_limits<uint32_t>::max() ||
        dest >= std::numeric_limits<uint32_t>::max())
      throw std::runtime_error("Invalid or unsupported 32-bit vertex ID at line " + std::to_string(line_number));
    edges.emplace_back(source, dest);
    edges.emplace_back(dest, source);
    largest = std::max(largest, std::max(source, dest));
  }
  if (edges.empty()) throw std::runtime_error("Input graph has no edges");
  std::sort(edges.begin(), edges.end());
  edges.erase(std::unique(edges.begin(), edges.end()), edges.end());
  CSR graph;
  graph.n = largest + 1;
  graph.offsets.resize(graph.n + 1);
  graph.neighbors.reserve(edges.size());
  for (const auto& edge : edges) {
    ++graph.offsets[std::get<0>(edge) + 1];
    graph.neighbors.push_back(std::get<1>(edge));
  }
  for (size_t i = 0; i < graph.n; ++i) {
    if (graph.offsets[i + 1]) graph.vertices.push_back(i);
    graph.offsets[i + 1] += graph.offsets[i];
  }
  std::cout << "BATCH_GRAPH {\"vertices\":" << graph.vertices.size()
            << ",\"vertex_universe\":" << graph.n << ",\"directed_edges\":"
            << edges.size() << "}" << std::endl;
  return graph;
}

inline uint64_t rss_bytes() {
  std::ifstream status("/proc/self/status");
  std::string key;
  while (status >> key) {
    if (key == "VmRSS:") {
      uint64_t value;
      status >> value;
      return value * 1024;
    }
    std::string rest;
    std::getline(status, rest);
  }
  throw std::runtime_error("VmRSS unavailable in /proc/self/status");
}

inline void memory(uint64_t before) {
  const auto after = rss_bytes();
  std::cout << std::setprecision(17)
            << "BATCH_MEMORY {\"metric\":\"graph_load_rss_delta\",\"rss_before_bytes\":" << before
            << ",\"rss_after_bytes\":" << after << ",\"memory_gib\":"
            << (static_cast<double>(after) - static_cast<double>(before)) / 1073741824.0
            << "}" << std::endl;
}

inline std::vector<uint32_t> permutation(size_t count) {
  std::vector<uint32_t> result(count);
  std::iota(result.begin(), result.end(), uint32_t{0});
  std::mt19937_64 random(42);
  std::shuffle(result.begin(), result.end(), random);
  return result;
}

template <class Insert, class Delete>
void run(const CSR& graph, unsigned trials, Insert insert, Delete remove) {
  if (!trials) throw std::runtime_error("Trials must be positive");
  std::mt19937_64 random(42);
  for (size_t batch : {10, 100, 1000, 10000}) {
    double insertion = 0, deletion = 0;
    for (unsigned trial = 0; trial < trials; ++trial) {
      std::vector<Edge> updates(batch);
      for (auto& edge : updates) {
        const auto source = graph.vertices[random() % graph.vertices.size()];
        const auto destination = graph.vertices[random() % graph.vertices.size()];
        edge = Edge(source, destination);
      }
      auto start = std::chrono::steady_clock::now();
      insert(updates);
      auto end = std::chrono::steady_clock::now();
      insertion += std::chrono::duration<double>(end - start).count();
      start = std::chrono::steady_clock::now();
      remove(updates);
      end = std::chrono::steady_clock::now();
      deletion += std::chrono::duration<double>(end - start).count();
    }
    std::cout << std::setprecision(17)
              << "BATCH_RESULT {\"batch_size\":" << batch << ",\"trials\":" << trials
              << ",\"insert_ops_s\":" << batch * trials / insertion
              << ",\"delete_ops_s\":" << batch * trials / deletion
              << ",\"insert_seconds\":" << insertion / trials
              << ",\"delete_seconds\":" << deletion / trials << "}" << std::endl;
  }
}
}  // namespace batch_benchmark
