from __future__ import annotations
import enum
import sys
import logging
import threading
from datetime import date
import time
from collections import OrderedDict
from colorama import Fore, Style, init
DEFAULT_PROGRESS_KEY = "+_+"
init(autoreset=True)

# Guards setLoggerClass() from concurrent mutation during get_logger() calls.
_logger_class_lock = threading.Lock()

class LogType(enum.IntEnum):
    ON = 0  # equal STDOUT
    OFF = 1
    STDOUT = 3
    FILE = 4
    BOTH = 5

class LogFmtType(enum.IntEnum):
    EMPTY = 0
    SHORT = 1
    LONG = 2
    BASIC = 3

def get_fmtstr(fmt:LogFmtType):
    if isinstance(fmt, LogFmtType) and fmt == LogFmtType.EMPTY:
        fmt = ''
        datefmt = ''
    elif isinstance(fmt, LogFmtType) and fmt == LogFmtType.SHORT:
        fmt = '%(asctime)s[%(name)s] %(message)s'
        datefmt = '%Y%m%dT%H%M%S'
    elif isinstance(fmt, LogFmtType) and fmt == LogFmtType.LONG:
        fmt = '%(asctime)s[%(name)s] %(levelname)s: %(message)s'
        datefmt = '%Y-%m-%d %H:%M:%S.%f'
    elif isinstance(fmt, LogFmtType) and fmt == LogFmtType.BASIC:
        fmt = logging.BASIC_FORMAT
        datefmt = '%Y-%m-%d %H:%M:%S'
    else:
        datefmt = '%Y-%m-%d %H:%M:%S'

    return fmt, datefmt

def get_logger(name: str, logtype: LogType = LogType.STDOUT, fmt: str | LogFmtType = LogFmtType.SHORT,
               level=logging.INFO, max_progress: int = 10, **fmtkwargs) -> CustomLogger:
    """
    Get a colored logger with the specified configuration.
    Use ColorFormatter to log colored message based on the log level as below:
        logging.DEBUG: Fore.GREEN
        logging.INFO: Fore.BLUE
        logging.WARNING: Fore.YELLOW
        logging.ERROR: Fore.RED
        logging.CRITICAL: Fore.RED + Style.BRIGHT

    Args:
        name (str): The name of the logger.
        logtype (LogType, optional): The type of logging. Defaults to LogType.STDOUT.
        fmt (str | LogFmtType, optional): The log message format. Defaults to LogFmtType.SHORT.
        level (int, optional): The logging level. Defaults to logging.INFO.
        **fmtkwargs: Additional keyword arguments for formatting the log message.

    Returns:
        CustomLogger: The configured logger instance, registered in the standard logging hierarchy.
    """
    # Register the logger in the standard logging hierarchy via setLoggerClass so that
    # logging.getLogger(name) always returns the SAME object as log.get_logger(name).
    # The lock prevents a race condition where another thread creates a plain Logger
    # with the same name while we have setLoggerClass active.
    with _logger_class_lock:
        _prev_class = logging.getLoggerClass()
        logging.setLoggerClass(CustomLogger)
        logger = logging.getLogger(name)
        logging.setLoggerClass(_prev_class)

    # If a plain Logger was already registered under this name (created before this
    # call via logging.getLogger() directly), upgrade it to CustomLogger in-place so
    # CustomLogger-specific attributes (progress spinner, etc.) are available.
    if not isinstance(logger, CustomLogger):
        logger.__class__ = CustomLogger
        logger.end = '\n'
        logger._max_progress = int(max_progress) if max_progress and int(max_progress) > 0 else 10
        logger._min_update_interval = 0.05
        logger._progress_states = OrderedDict()
    else:
        # Refresh the capacity setting in case the caller changes it.
        logger._max_progress = int(max_progress) if max_progress and int(max_progress) > 0 else 10

    # Clear existing handlers to avoid duplication when get_logger is called
    # multiple times for the same name (e.g., reconfiguring level at runtime).
    for handler in logger.handlers.copy():
        try:
            logger.removeHandler(handler)
        except ValueError:  # in case another thread has already removed it
            pass

    if isinstance(fmt, LogFmtType):
        fmt_str, datefmt = get_fmtstr(fmt)
    else:
        fmt_str = fmt
        datefmt = '%Y-%m-%d %H:%M:%S'

    if logtype == LogType.OFF:
        logger.addHandler(logging.NullHandler())
    if logtype in (LogType.ON, LogType.STDOUT, LogType.BOTH):
        handler = CustomHandler()
        handler.setFormatter(ColorFormatter(fmt=fmt_str, datefmt=datefmt))
        handler.setLevel(level)
        logger.addHandler(handler)
    if logtype in (LogType.FILE, LogType.BOTH):
        handler = logging.FileHandler(f'{name}_{date.today().strftime("%y%m%d")}.log')
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(fmt=fmt_str, datefmt=datefmt))
        logger.addHandler(handler)
    logger.setLevel(level)

    # Suppress propagation to the root logger only when this logger owns real
    # output handlers, preventing duplicate lines when setup_logging() is also
    # active on root.  LogType.OFF only adds a NullHandler, so propagation is
    # left enabled (messages simply go nowhere because NullHandler absorbs them).
    has_real_handlers = any(not isinstance(h, logging.NullHandler) for h in logger.handlers)
    logger.propagate = not has_real_handlers

    return logger

class ColorFormatter(logging.Formatter):
    """
    A custom logging formatter that adds color to log messages based on the log level.
    The default color is set as follows:
        logging.DEBUG: Fore.GREEN
        logging.INFO: Fore.BLUE
        logging.WARNING: Fore.YELLOW
        logging.ERROR: Fore.RED
        logging.CRITICAL: Fore.RED + Style.BRIGHT

    Attributes:
        FMT_COLOR (dict): A dictionary mapping log levels to color codes.

    Args:
        fmt (str): The format string for the log message.
        datefmt (str): The format string for the log message timestamp.
        **fmtkwargs: Additional keyword arguments to be passed to the base class constructor.
    """

    def __init__(self, fmt: str, datefmt: str, **fmtkwargs):
        self.FMT_COLOR = {
            logging.DEBUG: Fore.GREEN,
            logging.INFO: Fore.BLUE,
            logging.WARNING: Fore.YELLOW,
            logging.ERROR: Fore.RED,
            logging.CRITICAL: Fore.RED + Style.BRIGHT,
        }
        super().__init__(fmt=fmt, datefmt=datefmt, **fmtkwargs)

    def format(self, record)->str:
        """
        Formats the log record and adds color based on the log level.

        Args:
            record (logging.LogRecord): The log record to be formatted.

        Returns:
            str: The formatted log message with color added.
        """
        return self.FMT_COLOR.get(record.levelno, '') + super().format(record)

class CustomLogger(logging.Logger):
    """This class is a custom logger that change info method to accept 'end' parameter to add end of line character.
        whed end is not '\n', the log message will not add new line character at the end of the message."""
    progress_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

    def __init__(self, name, level='INFO', end='\n', max_progress: int = 10, min_update_interval: float = 0.05):
        super().__init__(name, level)
        self.end = end
        # Maximum number of concurrent progress entries to retain.
        self._max_progress = int(max_progress) if max_progress and int(max_progress) > 0 else 10
        # Minimum seconds between visible updates for the same key (rate-limit).
        self._min_update_interval = float(min_update_interval) if min_update_interval and float(min_update_interval) >= 0 else 0.0
        # Track progress state per key (default key is the msg string).
        # Use OrderedDict to keep insertion order so we can evict oldest entries.
        # Each state stores an index for the spinner, a start_time for elapsed calculation,
        # and last_update to implement rate-limiting.
        self._progress_states: OrderedDict = OrderedDict()

    def info(self, msg, *args, **kwargs):
        end = kwargs.pop('end', self.end)
        if self.isEnabledFor(logging.INFO):
            self._log(logging.INFO, msg, args, **kwargs, end=end)

    def progress(self, msg='', *args, **kwargs):
        """Log a message with level 'INFO' and rotate through progress_chars per "key".

        The progress state is tracked per `key`. By default the `key` is the
        `msg` value; callers may pass a `key=` kwarg to separate multiple
        concurrent progress bars that share the same message text.

        Each call updates the spinner character for that key and writes with
        `end='\r'` so the line is overwritten in-place.
        """
        # Extract and respect caller-provided `end` if any, avoid passing it twice.
        end = kwargs.pop('end', '\r')
        # Use explicit key when provided; otherwise use shared default key.
        key = kwargs.pop('key', DEFAULT_PROGRESS_KEY)
        now = time.perf_counter()
        # Acquire lock to inspect/create/update state atomically
        if key in self._progress_states:
            state = self._progress_states[key]
            # refresh recency
            try:
                self._progress_states.move_to_end(key)
            except Exception:
                pass
        else:
            # Evict oldest entry if exceeding configured capacity
            if len(self._progress_states) >= self._max_progress:
                try:
                    self._progress_states.popitem(last=False)
                except Exception:
                    first_key = next(iter(self._progress_states), None)
                    if first_key is not None:
                        self._progress_states.pop(first_key, None)
            state = {'idx': 0, 'start_time': now, 'last_update': 0.0}
            self._progress_states[key] = state

        # Rate-limit: skip update if called too soon for this key
        last = state.get('last_update', 0.0)
        if self._min_update_interval and (now - last) < self._min_update_interval:
            return

        # Advance spinner index and record last_update
        char_index = state['idx'] % len(self.progress_chars)
        char = self.progress_chars[char_index] + ' '
        state['idx'] = (state['idx'] + 1) % len(self.progress_chars)
        state['last_update'] = now

        # Emit the line (outside lock to avoid holding lock during I/O)
        self.info('\033[K' + char + msg, end=end, *args, **kwargs)

    def end_progress(self, msg='', *args, **kwargs):
        """End the progress animation for a given `key` (default key=msg).

        Callers should pass the same `msg` (or the same explicit `key=`) used in
        `progress()` to end that specific progress animation. If no matching
        progress state exists and `msg` is provided, the `msg` will be logged
        normally with a newline.
        """
        # Extract and respect caller-provided `end` if any, avoid passing it twice.
        end = kwargs.pop('end', '\n')
        # Resolve key: prefer explicit `key`, otherwise use shared default key.
        key = kwargs.pop('key', DEFAULT_PROGRESS_KEY)
        # Pop state under lock to avoid races
        state = self._progress_states.pop(key, None)

        if state is None:
            if msg:
                self.info(msg, end=end, *args, **kwargs)
            return

        start_time = state.get('start_time')
        if not start_time or msg == 'no_elapsed':
            return

        elapsed_time = ''
        time_spent = time.perf_counter() - start_time
        elapsed_time = f" (elapsed time:{time_spent:.2f}s)"
        final_msg = msg + elapsed_time if msg else elapsed_time
        self.info(final_msg, end=end, *args, **kwargs)

    def progress_ctx(self, msg: str, key: str | None = None, *, auto_start: bool = True):
        """Return a context manager that calls `progress` on enter and `end_progress` on exit.

        Usage:
            with logger.progress_ctx('task', key='id'):
                do_work()
        """
        logger = self

        class _ProgressCtx:
            def __init__(self, logger, msg, key):
                self.logger = logger
                self.msg = msg
                self.key = key

            def __enter__(self):
                if self.key is not None:
                    self.logger.progress(self.msg, key=self.key)
                else:
                    self.logger.progress(self.msg)
                return self

            def __exit__(self, exc_type, exc, tb):
                # Prefer a final message same as msg when exiting
                if self.key is not None:
                    self.logger.end_progress(self.msg, key=self.key)
                else:
                    self.logger.end_progress(self.msg)
                return False

        return _ProgressCtx(logger, msg, key)

    def _log(self, level, msg, args, exc_info=None, extra=None, stack_info=False, end='\n'):
        sinfo = None
        if logging._srcfile:
            if stack_info:
                # sinfo = traceback.extract_stack()[-4]
                fn, lno, func, sinfo = self.findCaller(stack_info)
            else:
                fn, lno, func = "(unknown file)", 0, "(unknown function)"
                sinfo = None
        if exc_info:
            if not isinstance(exc_info, tuple):
                exc_info = sys.exc_info()
        record = self.makeRecord(self.name, level, fn, lno, msg, args, exc_info, func, sinfo, extra)
        record.end = end
        self.handle(record)

class CustomHandler(logging.StreamHandler):
    def emit(self, record):
        msg = self.format(record)
        self.stream.write(msg + getattr(record, 'end', '\n'))
        self.flush()


# ---------------------------------------------------------------------------
# T-libs-001：LogConfig + setup_logging — 統一 root logger 設定入口
# ---------------------------------------------------------------------------
from dataclasses import dataclass as _dataclass, field as _field


@_dataclass
class LogConfig:
    """root logger 的設定物件，傳入 :func:`setup_logging` 使用。

    欄位說明：
        level    — logging 等級（int 或 logging.DEBUG/INFO/… 常數）。
        fmt      — 訊息格式（:class:`LogFmtType` 或自訂格式字串）。
        logtype  — 輸出目標（:class:`LogType`）。
        filename — 若 logtype 包含 FILE，此為輸出檔名；``None`` 表示依日期自動命名。
    """
    level: int = logging.INFO
    fmt: LogFmtType | str = _field(default_factory=lambda: LogFmtType.SHORT)
    logtype: LogType = LogType.STDOUT
    filename: str | None = None


def setup_logging(config: LogConfig | None = None) -> None:
    """依 :class:`LogConfig` 設定 root logger 的 handler、格式與 level。

    不修改任何已設定好的 child logger，僅操作 ``logging.root``。
    重複呼叫時會先清除 root 的既有 handler，再依新設定重建。

    Args:
        config: 設定物件；若為 ``None`` 則使用預設值（INFO / STDOUT / SHORT）。
    """
    if config is None:
        config = LogConfig()

    root = logging.getLogger()
    # 清除既有 handler，避免重複輸出
    for handler in root.handlers.copy():
        try:
            root.removeHandler(handler)
        except Exception:
            pass

    fmt_str, datefmt = get_fmtstr(config.fmt) if isinstance(config.fmt, LogFmtType) else (config.fmt, '%Y-%m-%d %H:%M:%S')

    if config.logtype in (LogType.ON, LogType.STDOUT, LogType.BOTH):
        handler: logging.Handler = CustomHandler()
        handler.setFormatter(ColorFormatter(fmt=fmt_str, datefmt=datefmt))
        handler.setLevel(config.level)
        root.addHandler(handler)

    if config.logtype in (LogType.FILE, LogType.BOTH):
        filename = config.filename or f'app_{date.today().strftime("%y%m%d")}.log'
        fhandler = logging.FileHandler(filename, encoding='utf-8')
        fhandler.setLevel(config.level)
        fhandler.setFormatter(logging.Formatter(fmt=fmt_str, datefmt=datefmt))
        root.addHandler(fhandler)

    if config.logtype == LogType.OFF:
        root.addHandler(logging.NullHandler())

    root.setLevel(config.level)


if __name__ == '__main__':

    _logger = get_logger(__name__, level=logging.DEBUG, logtype=LogType.STDOUT, fmt=LogFmtType.SHORT)
    _logger.debug('test')
    _logger.info('info')
    _logger.warning('warning')
    _logger.error('error')
    _logger.critical('critical')
    _logger.log(2, 'log' )
    for idx, value in enumerate(range(20)):
        _logger.progress(f'progress {idx}')
        import time
        time.sleep(0.05)
    _logger.end_progress('Done!')
