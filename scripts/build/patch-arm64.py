#!/usr/bin/env python3
"""Adapt pinned upstream x86 primitives for native ARM64 builds only.

Call after checkout, before copying variants or running configure. SIMD keeps
upstream algorithms through SIMDe; atomics retain sequential consistency.
"""
from pathlib import Path
import re
import subprocess
import sys


def replace(path, old, new, count=1):
    text = path.read_text()
    if old in new and text.count(new) >= count:
        return
    if text.count(old) == count:
        path.write_text(text.replace(old, new))
    elif text.count(old) == 0 and text.count(new) >= count:
        return
    else:
        raise SystemExit(f"{path}: source changed; review ARM64 compatibility patch")


def body(path, signature, replacement):
    text = path.read_text()
    start = text.index("{", text.index(signature))
    level = 1
    end = start + 1
    while level:
        level += (text[end] == "{") - (text[end] == "}")
        end += 1
    path.write_text(text[:start + 1] + "\n" + replacement + "\n" + text[end - 1:])


def simd(root):
    # AVX2 also includes the SSE4.1 operations used by Teseo and the unused
    # AVX helper signatures in Terrace headers. SIMDe maps these to NEON.
    pattern = re.compile(r"^#include <(?:immintrin|emmintrin|xmmintrin|pmmintrin|smmintrin|tmmintrin|nmmintrin|mmintrin|x86intrin)\.h>[^\n]*", re.M)
    header = "#ifndef SIMDE_ENABLE_NATIVE_ALIASES\n#define SIMDE_ENABLE_NATIVE_ALIASES\n#endif\n#include <simde/x86/avx2.h>"
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in (".hpp", ".h", ".cpp", ".cc") and "build" not in path.parts:
            text = path.read_text()
            patched = pattern.sub(header, text)
            if patched != text:
                path.write_text(patched)


def timestamp(path):
    replace(path, "#include <cinttypes>", "#include <cinttypes>\n#include <atomic>\n#include <ctime>")
    body(path, "inline uint64_t rdtscp()", """    // One system-wide monotonic clock, including across migrated threads.
    // Fences preserve the ordering role of x86 RDTSCP in epoch reclamation.
    std::atomic_thread_fence(std::memory_order_seq_cst);
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    std::atomic_thread_fence(std::memory_order_seq_cst);
    return static_cast<uint64_t>(now.tv_sec) * 1000000000ULL + now.tv_nsec;""")


def gfe(root):
    for common in (
        root / "third-party/libcommon/include/lib/common/optimisation.hpp",
        root / "graphlog-base/lib/common/include/lib/common/optimisation.hpp",
    ):
        timestamp(common)
        replace(common, '__asm__ __volatile__("": : :"memory");',
                "std::atomic_thread_fence(std::memory_order_seq_cst);")

    teseo = root / "teseo"
    simd(teseo / "include")
    simd(teseo / "src")
    assembly = teseo / "include/teseo/util/assembly.hpp"
    timestamp(assembly)
    body(assembly, "inline unsigned __int128 atomic_load_16(",
         "    return __atomic_load_n(&variable, __ATOMIC_SEQ_CST);")
    body(assembly, "inline void atomic_store_16(",
         "    __atomic_store_n(&variable, value, __ATOMIC_SEQ_CST);")
    latch = teseo / "include/teseo/util/latch.hpp"
    replace(latch, "/* memory order in case of success */ std::memory_order_release,",
            "/* memory order in case of success */ std::memory_order_acq_rel,", count=8)
    replace(latch, "bool is_version(uint64_t version) const {",
            "bool is_version(uint64_t version) const {\n        std::atomic_thread_fence(std::memory_order_seq_cst);")
    txlist = teseo / "src/transaction/transaction_list.cpp"
    replace(txlist, "version1 = m_version;",
            "std::atomic_thread_fence(std::memory_order_seq_cst);\n        version1 = m_version;", count=3)
    epoch = teseo / "include/teseo/context/thread_context.hpp"
    replace(epoch, "uint64_t m_epoch;", "std::atomic<uint64_t> m_epoch;")
    context = teseo / "src/context/thread_context.cpp"
    replace(context, "m_epoch = util::rdtscp();",
            "m_epoch = util::rdtscp();\n    std::atomic_thread_fence(std::memory_order_seq_cst);")
    segment = teseo / "src/memstore/segment.cpp"
    segment_text = segment.read_text()
    count = segment_text.count("uint64_t expected = m_latch;")
    if count:
        replace(segment, "uint64_t expected = m_latch;",
                "uint64_t expected = __atomic_load_n(&m_latch, __ATOMIC_SEQ_CST);", count=count)
    replace(segment, "m_latch = expected | MASK_WAIT; // unlock",
            "__atomic_store_n(&m_latch, expected | MASK_WAIT, __ATOMIC_SEQ_CST); // unlock")
    segment_header = teseo / "include/teseo/memstore/segment.hpp"
    replace(segment_header,
            "return (m_latch & (MASK_INVALID | MASK_WRITER | MASK_REBALANCER | MASK_VERSION)) == version;",
            "std::atomic_thread_fence(std::memory_order_seq_cst);\n    return (__atomic_load_n(&m_latch, __ATOMIC_SEQ_CST) & (MASK_INVALID | MASK_WRITER | MASK_REBALANCER | MASK_VERSION)) == version;")
    compiler = teseo / "include/teseo/util/compiler.hpp"
    replace(compiler, "#pragma once", "#pragma once\n#include <atomic>")
    replace(compiler, '__asm__ __volatile__("": : :"memory");',
            "std::atomic_thread_fence(std::memory_order_seq_cst);")
    # Publish queue entries and resized buffers with release/acquire ordering.
    # Upstream volatile and plain indices rely on x86 store ordering.
    queue = teseo / "include/teseo/gc/simple_queue.hpp"
    replace(queue, "uint32_t m_start;", "std::atomic<uint32_t> m_start;")
    replace(queue, "volatile uint32_t m_end;", "std::atomic<uint32_t> m_end;")
    for name, old, new in (
        ("transaction/transaction_list.hpp", "volatile uint64_t m_highest_writer_id", "std::atomic<uint64_t> m_highest_writer_id"),
        ("context/tc_list.hpp", "volatile uint32_t m_size", "std::atomic<uint32_t> m_size"),
    ):
        path = teseo / "include/teseo" / name
        replace(path, old, new)
    debug = teseo / "src/util/debug.cpp"
    replace(debug, '#include "teseo/util/debug.hpp"', '#include "teseo/util/debug.hpp"\n#include <csignal>')
    replace(debug, '__asm__ __volatile__ ("int $3");', 'std::raise(SIGTRAP);')

    simd(root / "sortledton/third-party")
    # GTX only needs cache prefetch hints, not SIMD emulation.
    for directory in (root / "GTX-SIGMOD2025/core", root / "GTX-SIGMOD2025/src"):
        for path in directory.iterdir():
            if path.suffix not in (".hpp", ".h", ".cpp"):
                continue
            text = path.read_text()
            text = re.sub(r"^#include <immintrin\.h>[^\n]*\n", "", text, flags=re.M)
            text = re.sub(r"_mm_prefetch\(([^\n]*?),\s*_MM_HINT_T2\)", r"__builtin_prefetch(\1, 0, 1)", text)
            path.write_text(text)
    replace(root / "GTX-SIGMOD2025/core/spin_latch.hpp", "__builtin_ia32_pause();",
            '__asm__ __volatile__("yield" ::: "memory");')
    replace(root / "GTX-SIGMOD2025/CMakeLists.txt",
            "target_link_libraries(gtx gtx_lib OpenMP::OpenMP_CXX TBB::tbb)",
            "target_link_libraries(gtx gtx_lib OpenMP::OpenMP_CXX TBB::tbb atomic)")
    # Keep arm64 images on the Armv8-A baseline even when built on newer CPUs.
    for path in (root / "configure.ac", teseo / "configure.ac"):
        replace(path, "MY_SET_CC_FLAG([_FLAGS], [-march=native])",
                "MY_SET_CC_FLAG([_FLAGS], [-march=armv8-a])")
    replace(root / "sortledton/CMakeLists.txt", "-march=native -mtune=native", "-march=armv8-a")
    subprocess.run([sys.executable, str(Path(__file__).with_name("patch-arm64-ordering.py")), str(root)], check=True)


def batch(root):
    terrace = root / "terrace"
    simd(terrace / "include")
    simd(terrace / "src")
    util = terrace / "include/util.h"
    for signature, replacement in (
        ("static inline int bsf_word(int word)", "  return __builtin_ctz(static_cast<unsigned>(word));"),
        ("static inline int bsr_word(int word)", "  return 31 - __builtin_clz(static_cast<unsigned>(word));"),
        ("static inline uint32_t bsr_word(uint32_t word)", "  return 31 - __builtin_clz(word);"),
        ("static inline uint64_t bsr_word(uint64_t word)", "  return 63 - __builtin_clzll(word);"),
    ):
        body(util, signature, replacement)
    for path in (terrace / "include/integerSort").rglob("utils.h"):
        body(path, "inline bool LCAS(long *ptr, long oldv, long newv)",
             "  return __atomic_compare_exchange_n(ptr, &oldv, newv, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);")
        body(path, "inline bool SCAS(int *ptr, int oldv, int newv)",
             "  return __atomic_compare_exchange_n(ptr, &oldv, newv, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);")
    stack = root / "aspen/code/pbbslib/concurrent_stack.h"
    replace(stack, "oldHead = head;", "oldHead.x = __atomic_load_n(&head.x, __ATOMIC_SEQ_CST);", count=2)
    replace(stack, "__sync_bool_compare_and_swap_16(&head.x,oldHead.x, newHead.x)",
            "__atomic_compare_exchange_n(&head.x, &oldHead.x, newHead.x, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST)", count=2)
    replace(stack, "return length(head.NC.node);", "CAS_t snapshot; snapshot.x = __atomic_load_n(&head.x, __ATOMIC_SEQ_CST);\n      return length(snapshot.NC.node);")


if __name__ == "__main__":
    mode, directory = sys.argv[1:]
    {"gfe": gfe, "batch": batch}[mode](Path(directory))
