#!/usr/bin/env python3
"""Repair shared publication paths in the pinned ARM64 benchmark sources.

Invoked only by patch-arm64.py. Preserve the upstream graph algorithms while
making their auxiliary views and background GC metadata safe on weakly ordered
CPUs. All source guards are checked before any files are written.
"""
from pathlib import Path
import sys


class Patches:
    def __init__(self):
        self.files = {}

    def replace(self, path, old, new, count=1):
        text = self.files.setdefault(path, path.read_text())
        # Check the complete replacement first: some insertions contain the
        # original function signature, so counting the old text alone is unsafe.
        if text.count(new) == count and old not in text.replace(new, ""):
            return
        if text.count(old) != count or new in text:
            raise SystemExit(f"{path}: source changed; review ARM64 ordering patch")
        self.files[path] = text.replace(old, new)

    def write(self):
        for path, text in self.files.items():
            if path.read_text() != text:
                path.write_text(text)


def patch(root):
    edits = Patches()
    transaction = root / "teseo/src/transaction/transaction_impl.cpp"
    # The ready-view fast path must acquire the builder's initialized contents.
    # Keep the raw member representation required by upstream's tagged CAS.
    for old, new in (
        ("return m_aux_view != nullptr;",
         "return __atomic_load_n(&m_aux_view, __ATOMIC_ACQUIRE) != nullptr;"),
        ("uint64_t value = reinterpret_cast<uint64_t>(m_aux_view);",
         "uint64_t value = reinterpret_cast<uint64_t>(__atomic_load_n(&m_aux_view, __ATOMIC_ACQUIRE));"),
        ("void* view = m_aux_view;",
         "void* view = __atomic_load_n(&m_aux_view, __ATOMIC_ACQUIRE);"),
        ("/* blah blah for non x86 archs */ true, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST",
         "/* Strong CAS: failure must return an actual competing view. */ false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST"),
        ("m_aux_view = (void*) views;",
         "__atomic_store_n(&m_aux_view, static_cast<void*>(views), __ATOMIC_RELEASE);"),
        ("""                views = reinterpret_cast<aux::View**>(&m_aux_view);
                m_global_context->aux_view(const_cast<TransactionImpl*>(this), views); // create the dynamic view""",
         """                aux::View* dynamic_view = nullptr;
                m_global_context->aux_view(const_cast<TransactionImpl*>(this), &dynamic_view); // create the dynamic view
                __atomic_store_n(&m_aux_view, static_cast<void*>(dynamic_view), __ATOMIC_RELEASE);"""),
        ("return aux_ret_ptr(m_aux_view, numa_aware); // we're done",
         "return aux_ret_ptr(__atomic_load_n(&m_aux_view, __ATOMIC_ACQUIRE), numa_aware); // we're done"),
        ("\n        view = m_aux_view;",
         "\n        view = __atomic_load_n(&m_aux_view, __ATOMIC_ACQUIRE);"),
    ):
        edits.replace(transaction, old, new)

    header = root / "sortledton/data-structure/TransactionManager.h"
    source = root / "sortledton/data-structure/TransactionManager.cpp"
    # The updater sorts/replaces a vector while worker threads query it. A
    # copied snapshot prevents references outliving the metadata mutex.
    edits.replace(header, "    mutex thread_registry_lock;",
                  "    mutex thread_registry_lock;\n    mutex snapshot_mutex;")
    edits.replace(header, "const vector<version_t>& get_sorted_versions();",
                  "vector<version_t> get_sorted_versions();")
    edits.replace(source, "const vector<version_t> &TransactionManager::get_sorted_versions()",
                  "vector<version_t> TransactionManager::get_sorted_versions()")
    for signature, first_line in (
        ("void TransactionManager::transactionCompleted(const Transaction &transaction)",
         "  if (transaction.get_version() != active_snapshots[thread_id] && transaction.get_commit_version() != active_snapshots[thread_id]) {"),
        ("version_t TransactionManager::getMinActiveVersion()", "  return min_version;"),
        ("void TransactionManager::update_min_version()",
         "  min_version = min(version.load(), *min_element(active_snapshots.begin(), active_snapshots.end()));"),
        ("version_t TransactionManager::draw_timestamp(bool commit_timestamp)",
         "  if (!commit_timestamp && active_snapshots[thread_id] != NO_TRANSACTION) {"),
        ("void TransactionManager::update_sorted_versions()", "  sorted_versions = active_snapshots;"),
        ("vector<version_t> TransactionManager::get_sorted_versions()", "  return sorted_versions;"),
    ):
        opening = signature + " {\n"
        edits.replace(source, opening + first_line,
                      opening + "  lock_guard<mutex> snapshot_guard(snapshot_mutex);\n" + first_line)
    for signature, first_line in (
        ("void TransactionManager::deregister_thread(size_t id)", "  if (!thread_id_in_use[id]) {"),
        ("void TransactionManager::reset_max_threads(uint max_threads)", "  for (bool in_use : thread_id_in_use) {"),
    ):
        # All methods touching both locks retain registry -> snapshot ordering.
        opening = signature + " {\n  lock_guard<mutex> l(thread_registry_lock);\n"
        edits.replace(source, opening + first_line,
                      opening + "  lock_guard<mutex> snapshot_guard(snapshot_mutex);\n" + first_line)
    edits.write()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch-arm64-ordering.py GFE_ROOT")
    patch(Path(sys.argv[1]))
