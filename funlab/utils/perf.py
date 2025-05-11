import inspect
import logging
from time import perf_counter
from functools import wraps
import time
from typing import Callable

from funlab.utils import log

mylogger = log.get_logger(__name__, level=logging.INFO)
class PerformanceTracker:
    """追蹤解析操作的性能"""
    def __init__(self):
        self.stats = {}
        self.cache_stats = {'hits': 0, 'misses': 0}  # 新增快取統計

    def __call__(self, func):
        """追蹤函數性能的裝飾器"""
        func_name = func.__name__

        def wrapper(*args, **kwargs):
            start_time = time.time()
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time

            if func_name not in self.stats:
                self.stats[func_name] = {
                    'calls': 0,
                    'total_time': 0.0,
                    'min_time': float('inf'),
                    'max_time': 0.0
                }

            self.stats[func_name]['calls'] += 1
            self.stats[func_name]['total_time'] += elapsed
            self.stats[func_name]['min_time'] = min(self.stats[func_name]['min_time'], elapsed)
            self.stats[func_name]['max_time'] = max(self.stats[func_name]['max_time'], elapsed)
            self.stats[func_name]['avg_time'] = (
                self.stats[func_name]['total_time'] / self.stats[func_name]['calls']
            )

            return result

        return wrapper

def track_time_print(func):
    """Performance tracking decorator, prints execution time"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        end = time.perf_counter()

        # Default: standalone function
        qualified_name = func.__name__

        # Check if this is a method call (first arg is an object instance)
        if args and hasattr(args[0], '__class__'):
            instance = args[0]
            cls = instance.__class__
            # Check if the function exists as an attribute in the class
            if hasattr(cls, func.__name__):
                qualified_name = f'{cls.__name__}.{func.__name__}'

        mylogger.info(f'{qualified_name} took {end-start:.4f} seconds')
        return result
    return wrapper

def track_time_stat(func: Callable) -> Callable:
    """Decorator to add 'performance_stats' attribute to track function execution time"""
    def set_performance_stats(obj, end, start):
        if not hasattr(obj, 'performance_stats'):
            setattr(obj, 'performance_stats', {})
        func_name = obj.__name__
        obj.performance_stats[func_name] = obj.performance_stats.get(func_name, {
            'calls': 0,
            'total_time': 0,
            'avg_time': 0
        })
        obj.performance_stats[func_name]['calls'] += 1
        obj.performance_stats[func_name]['total_time'] += (end - start)
        obj.performance_stats[func_name]['avg_time'] = (
            obj.performance_stats[func_name]['total_time'] /
            obj.performance_stats[func_name]['calls']
            )
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = perf_counter()
        result = func(*args, **kwargs)
        end = perf_counter()
        # Check if this is an instance method
        if args and inspect.ismethod(args[0]):
            obj = args[0]
        else:
            obj = func
        set_performance_stats(obj, end, start)
        return result
    return wrapper
