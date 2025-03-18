import inspect
from time import perf_counter
from functools import wraps
import time
from typing import Callable

def track_time_print(func):
    """Performance tracking decorator, prints execution time"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        end = time.perf_counter()
        print(f'{func.__name__} took {end-start:.4f} seconds')
        return result
    return wrapper

def track_time_stat(func: Callable) -> Callable:
    """Decorator to add 'performance_stats' attribute to track function execution time"""
    def set_performance_stats(obj, end, start):
        if not hasattr(obj, 'performance_stats'):
            setattr(obj, 'performance_stats', {})
        func_name = obj.__name__
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
