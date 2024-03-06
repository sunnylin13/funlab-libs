import enum
import logging
from datetime import date

from colorama import Fore, Style, init

init(autoreset=True)

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

def get_logger(name:str, logtype:LogType=LogType.STDOUT, fmt:str | LogFmtType=LogFmtType.SHORT
               , level=logging.ERROR, **fmtkwargs)->logging.Logger:
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
        level (int, optional): The logging level. Defaults to logging.ERROR.
        **fmtkwargs: Additional keyword arguments for formatting the log message.

    Returns:
        logging.Logger: The configured logger instance.
    """

    logger = logging.getLogger(name)
    logger.propagate = False # disable propagate so the parent, e.g. webserver waitress, will not show my log again
    for handler in logger.handlers.copy():
        try:
            logger.removeHandler(handler)
        except ValueError:  # in case another thread has already removed it
            pass
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

    if logtype == LogType.OFF:
        logger.addHandler(logging.NullHandler())
        # logger.propagate = False
    elif logtype in (LogType.ON, LogType.STDOUT, LogType.BOTH):
        handler = logging.StreamHandler()
        handler.setFormatter(ColorFormatter(fmt=fmt, datefmt=datefmt))
        handler.setLevel(level)
        logger.addHandler(handler)
    elif logtype in (LogType.FILE, LogType.BOTH):
        handler = logging.FileHandler(f'{name}_{date.today().strftime("%y%m%d")}.log')
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
    logger.setLevel(level)
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


if __name__ == '__main__':
    _logger = get_logger(__name__)
    _logger.debug('test')
    _logger.info('info')
    _logger.warning('warning')
    _logger.error('error')
    _logger.critical('critical')
    _logger.log(2, 'log' )
