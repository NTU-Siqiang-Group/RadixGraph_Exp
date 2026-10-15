#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>

namespace gfe::experiment::details {

// Smoke fixtures can finish during the upstream one-second startup sleep.
// Park updates at warmup until the reader is ready, leaving actual updates
// to overlap the first read round. Paper runs leave this coordination disabled.
class SmokeConcurrentStart {
    const bool m_enabled = [] {
        const char* value = std::getenv("RG_SMOKE_CONCURRENT_START");
        return value != nullptr && std::strcmp(value, "1") == 0;
    }();
    mutable std::atomic<uint64_t> m_completed{0};
    mutable std::atomic<uint64_t> m_total{0};
    mutable std::atomic<bool> m_graph_ready{false};
    mutable std::atomic<bool> m_readers_ready{false};
    mutable std::mutex m_mutex;
    mutable std::condition_variable m_changed;

public:
    bool enabled() const { return m_enabled; }
    bool graph_ready() const { return m_graph_ready.load(std::memory_order_acquire); }

    double progress() const {
        const auto total = m_total.load(std::memory_order_acquire);
        return total == 0 ? 0.0 :
            static_cast<double>(m_completed.load(std::memory_order_acquire)) / total;
    }

    void checkpoint(uint64_t completed, uint64_t total, const std::atomic<bool>& stop) const {
        if (!m_enabled || total == 0) return;
        m_total.store(total, std::memory_order_release);
        auto previous = m_completed.load(std::memory_order_relaxed);
        while (previous < completed && !m_completed.compare_exchange_weak(
                   previous, completed, std::memory_order_release, std::memory_order_relaxed)) {}

        const auto warmup = total / 10 + (total % 10 != 0);
        if (completed >= warmup && completed < total &&
            !m_readers_ready.load(std::memory_order_acquire)) {
            std::unique_lock<std::mutex> lock(m_mutex);
            m_graph_ready.store(true, std::memory_order_release);
            while (!m_readers_ready.load(std::memory_order_acquire) && !stop.load()) {
                // The aging timeout can stop workers without notifying this gate.
                m_changed.wait_for(lock, std::chrono::milliseconds(10));
            }
        }
    }

    void release_readers() const {
        if (!m_enabled) return;
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            m_readers_ready.store(true, std::memory_order_release);
        }
        m_changed.notify_all();
    }

    class ReleaseGuard {
        const SmokeConcurrentStart& m_start;
    public:
        explicit ReleaseGuard(const SmokeConcurrentStart& start) : m_start(start) {}
        ~ReleaseGuard() { m_start.release_readers(); }
        ReleaseGuard(const ReleaseGuard&) = delete;
        ReleaseGuard& operator=(const ReleaseGuard&) = delete;
    };
};

} // namespace gfe::experiment::details
