"""LIB-03 / LIB-18：jinja filters 正確性。"""
from datetime import datetime

from funlab.core.jinja_filters import slope2angle, timestamp_natation
from funlab.utils.dtts import local_datetime2utc_timestamp


def _ts(y, m, d=15):
    return local_datetime2utc_timestamp(datetime(y, m, d))


def test_quarter_filter_month_3_is_q1():
    # PLAN (e) 原測試誤寫為 '3'；(d) 公式與 dtts.quarter_of_date(ceil(m/3)) 一致口徑下 3 月 = Q1
    assert timestamp_natation(_ts(2024, 3), '%q') == '1'


def test_quarter_filter_month_6_is_q2():
    assert timestamp_natation(_ts(2024, 6), '%q') == '2'


def test_quarter_filter_month_9_is_q3():
    assert timestamp_natation(_ts(2024, 9), '%q') == '3'


def test_quarter_filter_month_12_is_q4():
    assert timestamp_natation(_ts(2024, 12), '%q') == '4'


def test_quarter_filter_month_4_is_q2():
    # 原文件既有的宣告例子：2023-04-01 → Q2
    assert timestamp_natation(_ts(2023, 4, 1), '%q') == '2'


def test_quarter_mixed_with_normal_format():
    assert timestamp_natation(_ts(2024, 12), '%Y-Q%q') == '2024-Q4'


def test_slope2angle_returns_string_of_degrees():
    assert slope2angle(1.0) == '45.000'


def test_slope2angle_none_is_na():
    assert slope2angle(None) == 'NA'
