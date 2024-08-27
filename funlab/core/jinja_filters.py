
from datetime import date, datetime, time, timedelta
from enum import Enum
import math
from funlab.utils import dtts

__all__ = ['timestamp_natation', 'common_formatter', 'slope2angle']

def timestamp_natation(timestamp:float, formatstr:str='%Y-%m-%d %H:%M:%S')->str:
    """
    Converts a float timestamp() value to a formatted notation string in python.
    And, provide extra notation %q, this prsent the quarter number, e.g., %q got 2 for 2023-04-01. No %Q needed and supported.

    Args:
        timestamp (float): The timestamp float value to convert.
        formatstr (str, optional): The format string for the notation. Defaults to '%Y-%m-%d %H:%M:%S'.

    Returns:
        str: The formatted notation string.
    """
    ddate = dtts.utc_timestamp2local_datetime(timestamp)
    formatstr = formatstr.replace('%q', f'{ddate.month//3+1}')
    notation = ddate.strftime(formatstr)
    return notation

def common_formatter(value:any)->str:
    """
    A Jinjia Common value filter to format the given value based on its type.

    Args:
        value: The value to be formatted.

    Returns:
        str: The formatted value.
    """
    if isinstance(value, float):
        try:
            if value == (int(value)):
                return f'{value:,}'
            else:
                return f'{value:,.3f}'
        except Exception: # OverflowError: 'nan', 'inf'
                return str(value)
    elif isinstance(value, int):
        return f'{value:,}'
    elif isinstance(value, Enum):
        return value.name
    elif isinstance(value, (datetime, date,)):
        if isinstance(value, datetime) and value - datetime.combine(value.date(), time(0, 0, 0)) == timedelta(0):
            value = value.date()
        return value.isoformat()
    # elif isinstance(value, pd.Timestamp):  暫不import & 處理 pandas.Timestamp type
    #     return value.isoformat()
    elif value is None:
        return 'NA'
    else:
        return str(value)

def slope2angle(slope:float)->float:
    """
    Convert the slope value to the angle in degrees.

    Args:
        slope (float): The slope value.

    Returns:
        float: The angle in degrees.
    """
    if slope is None:
        return 'NA'
    else:
        degree = math.degrees(math.atan(slope))
        return f'{degree:,.3f}'