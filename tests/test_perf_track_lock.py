"""LIB-13：PerformanceTracker 併發更新計數正確、不拋錯。"""
import threading

from funlab.utils.perf_track import PerformanceTracker


def test_concurrent_updates_count_exactly():
    t = PerformanceTracker()
    N, T = 5_000, 8

    def worker():
        f = lambda: None
        for _ in range(N):
            t._update_stats(f, 0.0001, ())

    threads = [threading.Thread(target=worker) for _ in range(T)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert t.stats["<lambda>"]["calls"] == N * T


def test_cache_hit_miss_counts():
    t = PerformanceTracker()
    for _ in range(100):
        t.record_cache_hit()
        t.record_cache_miss()
    assert t.cache_stats == {"hits": 100, "misses": 100}
    assert t.get_cache_hit_rate() == 0.5
