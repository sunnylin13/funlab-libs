import inspect
import logging
import contextlib
from time import perf_counter
from functools import wraps
from typing import Callable, Dict, Any, Optional
from collections import defaultdict

from funlab.utils import log

mylogger = log.get_logger(__name__, level=logging.INFO)

class PerformanceTracker:
    """統一的性能追蹤器"""

    def __init__(self, enable_cache_stats: bool = True):
        self.stats = defaultdict(lambda: {
            'calls': 0,
            'total_time': 0.0,
            'min_time': float('inf'),
            'max_time': 0.0,
            'avg_time': 0.0
        })
        self.cache_stats = {'hits': 0, 'misses': 0} if enable_cache_stats else None

    def record_cache_hit(self):
        """記錄快取命中"""
        if self.cache_stats:
            self.cache_stats['hits'] += 1

    def record_cache_miss(self):
        """記錄快取未命中"""
        if self.cache_stats:
            self.cache_stats['misses'] += 1

    def get_cache_hit_rate(self) -> float:
        """獲取快取命中率"""
        if not self.cache_stats:
            return 0.0
        total = self.cache_stats['hits'] + self.cache_stats['misses']
        return self.cache_stats['hits'] / total if total > 0 else 0.0

    def __call__(self, func: Callable) -> Callable:
        """追蹤函數性能的裝飾器"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = perf_counter() - start_time
                self._update_stats(func, elapsed, args)
        return wrapper

    def _update_stats(self, func: Callable, elapsed: float, args: tuple):
        """更新統計資料"""
        func_name = self._get_qualified_name(func, args)

        stats = self.stats[func_name]
        stats['calls'] += 1
        stats['total_time'] += elapsed
        stats['min_time'] = min(stats['min_time'], elapsed)
        stats['max_time'] = max(stats['max_time'], elapsed)
        stats['avg_time'] = stats['total_time'] / stats['calls']

    def _get_qualified_name(self, func: Callable, args: tuple) -> str:
        """獲取函數的完整名稱"""
        func_name = func.__name__

        # 檢查是否為方法調用
        if args and hasattr(args[0], '__class__'):
            instance = args[0]
            cls = instance.__class__
            if hasattr(cls, func_name):
                return f'{cls.__name__}.{func_name}'

        return func_name

    def get_stats(self, func_name: Optional[str] = None) -> Dict[str, Any]:
        """獲取統計資料"""
        if func_name:
            return dict(self.stats.get(func_name, {}))
        return {name: dict(stats) for name, stats in self.stats.items()}

    def reset_stats(self):
        """重置統計資料"""
        self.stats.clear()
        if self.cache_stats:
            self.cache_stats = {'hits': 0, 'misses': 0}

    def print_summary(self):
        """印出性能摘要"""
        if not self.stats:
            mylogger.info("No performance data available")
            return

        mylogger.info("=== Performance Summary ===")
        for func_name, stats in sorted(self.stats.items()):
            mylogger.info(
                f"{func_name}: "
                f"calls={stats['calls']}, "
                f"total={stats['total_time']:.4f}s, "
                f"avg={stats['avg_time']:.4f}s, "
                f"min={stats['min_time']:.4f}s, "
                f"max={stats['max_time']:.4f}s"
            )

        if self.cache_stats:
            hit_rate = self.get_cache_hit_rate()
            mylogger.info(f"Cache hit rate: {hit_rate:.2%}")

    def record_block_time(self, block_name: str, elapsed: float):
        """記錄程式區塊的執行時間"""
        # 創建一個模擬函數來重用統計邏輯
        class MockFunc:
            def __init__(self, name):
                self.__name__ = name

        mock_func = MockFunc(block_name)
        self._update_stats(mock_func, elapsed, ())


class TimeTracker:
    """時間追蹤的 Context Manager"""

    def __init__(self,
                 name: str,
                 print_result: bool = True,
                 tracker: Optional[PerformanceTracker] = None,
                 logger=None):
        self.name = name
        self.print_result = print_result
        self.tracker = tracker
        self.logger = logger or mylogger
        self.start_time = None
        self.elapsed = None

    def __enter__(self):
        self.start_time = perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.elapsed = perf_counter() - self.start_time

        if self.print_result:
            self.logger.info(f'{self.name} took {self.elapsed:.4f} seconds')

        if self.tracker:
            self.tracker.record_block_time(self.name, self.elapsed)


@contextlib.contextmanager
def track_time_block(name: str,
                    print_result: bool = True,
                    tracker: Optional[PerformanceTracker] = None):
    """簡化的程式區塊時間追蹤 context manager"""
    start_time = perf_counter()
    try:
        yield
    finally:
        elapsed = perf_counter() - start_time
        if print_result:
            mylogger.info(f'{name} took {elapsed:.4f} seconds')
        if tracker:
            tracker.record_block_time(name, elapsed)


@contextlib.contextmanager
def time_section(section_name: str,
                tracker: Optional[PerformanceTracker] = None,
                print_start: bool = True,
                print_end: bool = True):
    """為一整個程式段落進行命名和追蹤"""
    if tracker is None:
        tracker = default_tracker

    if print_start:
        mylogger.info(f"=== Starting {section_name} ===")

    start_time = perf_counter()

    try:
        yield tracker
    finally:
        elapsed = perf_counter() - start_time
        if print_end:
            mylogger.info(f"=== {section_name} completed in {elapsed:.4f} seconds ===")
        tracker.record_block_time(section_name, elapsed)


def time_it(name: str, print_result: bool = True, tracker: Optional[PerformanceTracker] = None):
    """最簡化的時間追蹤 context manager"""
    return track_time_block(name, print_result, tracker)


def track_time(
    print_result: bool = True,
    store_stats: bool = False,
    tracker: Optional[PerformanceTracker] = None
) -> Callable:
    """
    統一的時間追蹤裝飾器

    Args:
        print_result: 是否印出執行時間
        store_stats: 是否儲存統計到函數屬性
        tracker: 使用特定的 PerformanceTracker 實例
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = perf_counter() - start_time

                # 獲取函數名稱
                func_name = func.__name__
                if args and hasattr(args[0], '__class__'):
                    instance = args[0]
                    cls = instance.__class__
                    if hasattr(cls, func_name):
                        func_name = f'{cls.__name__}.{func_name}'

                # 印出結果
                if print_result:
                    mylogger.info(f'{func_name} took {elapsed:.4f} seconds')

                # 儲存統計
                if store_stats:
                    if not hasattr(func, 'performance_stats'):
                        func.performance_stats = defaultdict(lambda: {
                            'calls': 0, 'total_time': 0.0, 'avg_time': 0.0
                        })

                    stats = func.performance_stats[func_name]
                    stats['calls'] += 1
                    stats['total_time'] += elapsed
                    stats['avg_time'] = stats['total_time'] / stats['calls']

                # 使用外部 tracker
                if tracker:
                    tracker._update_stats(func, elapsed, args)

        return wrapper
    return decorator


# 全域預設追蹤器
default_tracker = PerformanceTracker()

# 便利的裝飾器別名
performance_tracker = default_tracker

# ==================== 使用範例 ====================

def basic_example():
    from funlab.utils.perf_track import PerformanceTracker, track_time

    # 創建性能追蹤器
    tracker = PerformanceTracker()
    @tracker
    def slow_function():
        import time
        time.sleep(0.1)
        return "done"

    @tracker
    def fast_function():
        return sum(range(1000))

    # 執行函數
    for _ in range(5):
        slow_function()
        fast_function()

    # 查看統計
    tracker.print_summary()
    # 輸出:
    # === Performance Summary ===
    # fast_function: calls=5, total=0.0012s, avg=0.0002s, min=0.0002s, max=0.0003s
    # slow_function: calls=5, total=0.5023s, avg=0.1005s, min=0.1002s, max=0.1008s

    # 只印出時間，不儲存統計
    @track_time(print_result=True, store_stats=False)
    def quick_task():
        return sum(range(100))

    # 只儲存統計，不印出
    @track_time(print_result=False, store_stats=True)
    def background_task():
        import time
        time.sleep(0.01)
        return "completed"

    # 同時印出和儲存
    @track_time(print_result=True, store_stats=True)
    def important_task():
        return "critical operation"

    # 使用外部追蹤器
    external_tracker = PerformanceTracker()

    @track_time(print_result=False, tracker=external_tracker)
    def monitored_task():
        return "monitored"

    # 執行
    quick_task()  # 只會印出時間
    background_task()  # 只會儲存統計
    important_task()  # 印出時間並儲存統計
    monitored_task()  # 記錄到外部追蹤器

    # 查看函數自身的統計
    print(background_task.performance_stats)
    print(important_task.performance_stats)

    # 查看外部追蹤器統計
    external_tracker.print_summary()

def class_method_example():
    from funlab.utils.perf_track import performance_tracker
    class DataProcessor:
        @performance_tracker
        def process_data(self, data):
            # 模擬數據處理
            return [x * 2 for x in data]

        @performance_tracker
        def save_data(self, data):
            # 模擬保存數據
            import time
            time.sleep(0.05)
            return len(data)

    # 使用
    processor = DataProcessor()
    data = list(range(1000))

    processor.process_data(data)
    processor.save_data(data)

    # 查看統計（會顯示類別名稱）
    performance_tracker.print_summary()
    # 輸出:
    # === Performance Summary ===
    # DataProcessor.process_data: calls=1, total=0.0008s, avg=0.0008s, min=0.0008s, max=0.0008s
    # DataProcessor.save_data: calls=1, total=0.0502s, avg=0.0502s, min=0.0502s, max=0.0502s

def cache_stat_example():
    from funlab.utils.perf_track import PerformanceTracker
    class CachedService:
        def __init__(self):
            self.cache = {}
            self.tracker = PerformanceTracker()

        @property
        def performance_tracker(self):
            return self.tracker

        def get_data(self, key):
            if key in self.cache:
                self.tracker.record_cache_hit()
                return self.cache[key]
            else:
                self.tracker.record_cache_miss()
                # 模擬數據獲取
                import time
                time.sleep(0.01)
                value = f"data_for_{key}"
                self.cache[key] = value
                return value

    # 使用
    service = CachedService()

    # 第一次調用 - cache miss
    service.get_data("key1")
    service.get_data("key2")

    # 第二次調用 - cache hit
    service.get_data("key1")
    service.get_data("key2")

    # 查看快取統計
    print(f"Cache hit rate: {service.tracker.get_cache_hit_rate():.2%}")
    # 輸出: Cache hit rate: 50.00%

def context_manager_examples():
    """Context Manager 使用範例"""

    # 1. 基本的程式區塊追蹤
    with track_time_block("database_query"):
        import time
        time.sleep(0.1)  # 模擬數據庫查詢
        result = "query result"

    # 2. 使用 TimeTracker 類別（可獲取執行時間）
    tracker = PerformanceTracker()
    with TimeTracker("complex_calculation", tracker=tracker) as timer:
        import time
        time.sleep(0.05)
        calculation_result = sum(range(10000))

    print(f"計算耗時: {timer.elapsed:.4f} 秒")

    # 3. 使用 time_section 追蹤整個段落
    with time_section("Data Processing Pipeline", tracker) as section_tracker:

        with time_it("data_loading"):
            # 模擬數據載入
            import time
            time.sleep(0.02)
            data = list(range(1000))

        with time_it("data_transformation", tracker=section_tracker):
            # 模擬數據轉換
            transformed_data = [x * 2 for x in data]

        with time_it("data_validation", tracker=section_tracker):
            # 模擬數據驗證
            valid_data = [x for x in transformed_data if x > 0]

    # 查看統計
    tracker.print_summary()

def mixed_tracking_example():
    """混合使用裝飾器和 Context Manager 的範例"""

    tracker = PerformanceTracker()

    @tracker
    def process_data(data):
        """使用裝飾器追蹤整個函數"""

        # 在函數內部使用 context manager 追蹤特定區塊
        with time_it("data_validation", tracker=tracker):
            # 驗證數據
            if not data:
                raise ValueError("Empty data")

        with time_it("data_transformation", tracker=tracker):
            # 轉換數據
            transformed = []
            for item in data:
                with time_it("single_item_processing", print_result=False, tracker=tracker):
                    transformed.append(item * 2)

        with time_it("result_aggregation", tracker=tracker):
            # 聚合結果
            return sum(transformed)

    # 執行
    data = list(range(100))
    result = process_data(data)

    # 查看統計
    tracker.print_summary()


def performance_comparison_example():
    """性能比較範例"""

    tracker1 = PerformanceTracker()
    tracker2 = PerformanceTracker()

    # 比較兩種不同的算法
    with time_it("algorithm_1", tracker=tracker1):
        # 算法1: 使用列表推導
        result1 = [x**2 for x in range(10000)]

    with time_it("algorithm_2", tracker=tracker2):
        # 算法2: 使用普通循環
        result2 = []
        for x in range(10000):
            result2.append(x**2)

    print("算法1統計:")
    tracker1.print_summary()
    print("\n算法2統計:")
    tracker2.print_summary()


if __name__ == "__main__":
    print("=== Basic Example ===")
    basic_example()

    print("\n=== Class Method Example ===")
    class_method_example()

    print("\n=== Cache Stat Example ===")
    cache_stat_example()

    print("=== Context Manager Examples ===")
    context_manager_examples()

    print("\n=== Mixed Tracking Example ===")
    mixed_tracking_example()

    print("\n=== Performance Comparison Example ===")
    performance_comparison_example()