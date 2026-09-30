from pathlib import Path
import os
import time

from core.cache_store import CacheStore


def test_cache_store_never_evicts_pinned_generation(tmp_path):
    store = CacheStore(tmp_path / "cache")
    old = store.layout.reusable / "old.parquet"
    keep = store.layout.reusable / "keep.parquet"
    old.write_bytes(b"old")
    keep.write_bytes(b"keep")
    stale = time.time() - 31 * 86400
    os.utime(old, (stale, stale)); os.utime(keep, (stale, stale))
    store.pin(keep)
    store.evict()
    assert not old.exists()
    assert keep.exists()
    store.unpin(keep)
    store.evict()
    assert not keep.exists()


def test_cache_layout_separates_reusable_results_and_process_temp(tmp_path):
    store = CacheStore(tmp_path / "cache")
    assert store.layout.reusable != store.layout.results
    assert store.layout.temporary != store.layout.session
    assert str(os.getpid()) in store.layout.session.name
