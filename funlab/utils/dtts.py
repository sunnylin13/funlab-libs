
import calendar
import math
from datetime import date, datetime, time, timedelta, timezone
from dateutil.relativedelta import relativedelta
import pandas as pd

LOCAL_TZ = datetime.now(timezone.utc).astimezone().tzinfo
TW_TZ = timezone(timedelta(seconds=28800), name='Asia/Taipei')

def local_datetime2utc_timestamp(dt:datetime|date)->float:

    if type(dt) == date:  # isinstance(dt, date) will be true, if dt is a datetime
        dt = datetime.combine(dt, time=time(0, 0, 0))
    return dt.replace(tzinfo=LOCAL_TZ).astimezone(tz=timezone.utc).timestamp()

def utc_timestamp2local_datetime(ts:float)->datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz=LOCAL_TZ).replace(tzinfo=None)

def local_timestamp2utc_timestamp(ts:float):
    return datetime.fromtimestamp(ts, tz=LOCAL_TZ).astimezone(tz=timezone.utc).timestamp()

def quarter_of_date(ddate: datetime|date, shift_quarter_count=0)->int:
    """ get ddate's quarter number
    """
    if shift_quarter_count!=0:
        ddate, _ = quarter_start_end_date(ddate, shift_quarter_count)
    return math.ceil(ddate.month / 3)

def quarter_start_end_date(fromdate:date, shift_quarter_count:int=0)->date:
    """取得傳入日期所在季度的起始及最後一天, 例 1 - 3 月間的日期, 得到1/1及 3/31

    Args:
        fromdate ([date]): 傳入日期, 再shift個季度後, 取得所在季度的起始及最後一天
        shift_quarter_count: 由 fromdate, 往前移shift_quarter_count<0個季度, 或往後移shift_quarter_count>0個季度, =0 即fromdate的當前季度
    """
    quarter_year = fromdate.year
    quarter_end_month = math.ceil(fromdate.month / 3) * 3 + shift_quarter_count * 3
    if quarter_end_month <= 0:
        while quarter_end_month <= 0:
            quarter_end_month += 12
            quarter_year -= 1
    elif quarter_end_month > 12:
        while quarter_end_month > 12:
            quarter_end_month -= 12
            quarter_year += 1
    quarter_start_month = quarter_end_month - 2
    quarter_start_date = date(quarter_year, quarter_start_month, day=1)
    quarter_end_date = date(quarter_year, quarter_end_month, day=30 if quarter_end_month in (4, 6, 9, 11) else 31)
    return quarter_start_date, quarter_end_date

def quarter_start_date(ddate:date)->date:
    quarter = quarter_of_date(ddate)
    quarter_start_month = 1 if quarter == 1 else 4 if quarter == 2 else 7 if quarter == 3 else 10
    return date(ddate.year, quarter_start_month, 1)

def quarter_end_date(ddate:date)->date:
    quarter = quarter_of_date(ddate)
    quarter_end_month = 3 if quarter == 1 else 6 if quarter == 2 else 9 if quarter == 3 else 12
    return date(ddate.year, quarter_end_month, calendar.monthrange(ddate.year, quarter_end_month)[1])

def quarter_start_date2(year:int, quarter:int)->date:
    quarter_start_month = 1 if quarter == 1 else 4 if quarter == 2 else 7 if quarter == 3 else 10
    return date(year, quarter_start_month, 1)

def quarter_end_date2(year:int, quarter:int)->date:
    quarter_end_month = 3 if quarter == 1 else 6 if quarter == 2 else 9 if quarter == 3 else 12
    return date(year, quarter_end_month, calendar.monthrange(year, quarter_end_month)[1])

