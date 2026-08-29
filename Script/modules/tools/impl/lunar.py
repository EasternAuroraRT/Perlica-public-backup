from __future__ import annotations
from typing import *  # pyright: ignore[reportWildcardImportFromLibrary]
from datetime import date, datetime

# ==================== 农历数据表（1900-2100） ====================
# 每个元素为一个 int，位含义：
#   bit 0-3   : 闰月月份（0 表示无闰月）
#   bit 4-15  : 12 个普通月的大小（1 表示 30 天，0 表示 29 天），bit4 对应腊月，bit15 对应正月
#   bit 16    : 闰月大小（1 表示 30 天，0 表示 29 天）
_LUNAR_INFO = [
    0x04bd8, 0x04ae0, 0x0a570, 0x054d5, 0x0d260, 0x0d950, 0x16554, 0x056a0, 0x09ad0, 0x055d2,
    0x04ae0, 0x0a5b6, 0x0a4d0, 0x0d250, 0x1d255, 0x0b540, 0x0d6a0, 0x0ada2, 0x095b0, 0x14977,
    0x04970, 0x0a4b0, 0x0b4b5, 0x06a50, 0x06d40, 0x1ab54, 0x02b60, 0x09570, 0x052f2, 0x04970,
    0x06566, 0x0d4a0, 0x0ea50, 0x06e95, 0x05ad0, 0x02b60, 0x186e3, 0x092e0, 0x1c8d7, 0x0c950,
    0x0d4a0, 0x1d8a6, 0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2, 0x0a950, 0x0b557,
    0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5d0, 0x14573, 0x052d0, 0x0a9a8, 0x0e950, 0x06aa0,
    0x0aea6, 0x0ab50, 0x04b60, 0x0aae4, 0x0a570, 0x05260, 0x0f263, 0x0d950, 0x05b57, 0x056a0,
    0x096d0, 0x04dd5, 0x04ad0, 0x0a4d0, 0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b5a0, 0x195a6,
    0x095b0, 0x049b0, 0x0a974, 0x0a4b0, 0x0b27a, 0x06a50, 0x06d40, 0x0af46, 0x0ab60, 0x09570,
    0x04af5, 0x04970, 0x064b0, 0x074a3, 0x0ea50, 0x06b58, 0x055c0, 0x0ab60, 0x096d5, 0x092e0,
    0x0c960, 0x0d954, 0x0d4a0, 0x0da50, 0x07552, 0x056a0, 0x0abb7, 0x025d0, 0x092d0, 0x0cab5,
    0x0a950, 0x0b4a0, 0x0baa4, 0x0ad50, 0x055d9, 0x04ba0, 0x0a5b0, 0x15176, 0x052b0, 0x0a930,
    0x07954, 0x06aa0, 0x0ad50, 0x05b52, 0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260, 0x0ea65, 0x0d530,
    0x05aa0, 0x076a3, 0x096d0, 0x04bd7, 0x04ad0, 0x0a4d0, 0x1d0b6, 0x0d250, 0x0d520, 0x0dd45,
    0x0b5a0, 0x056d0, 0x055b2, 0x049b0, 0x0a577, 0x0a4b0, 0x0aa50, 0x1b255, 0x06d20, 0x0ada0,
    0x14b63, 0x09370, 0x049f8, 0x04970, 0x064b0, 0x168a6, 0x0ea50, 0x06b20, 0x1a6c4, 0x0aae0,
    0x0a2e0, 0x0d2e3, 0x0c960, 0x0d557, 0x0d4a0, 0x0da50, 0x05d55, 0x056a0, 0x0a6d0, 0x055d4,
    0x052d0, 0x0a9b8, 0x0a950, 0x0b4a0, 0x0b6a6, 0x0ad50, 0x055a0, 0x0aba4, 0x0a5b0, 0x052b0,
    0x0b273, 0x06930, 0x07337, 0x06aa0, 0x0ad50, 0x14b55, 0x04b60, 0x0a570, 0x054e4, 0x0d160,
    0x0e968, 0x0d520, 0x0daa0, 0x16aa6, 0x056d0, 0x04ae0, 0x0a9d4, 0x0a2d0, 0x0d150, 0x0f252,
    0x0d520,
]

# ==================== 中文名称 ====================
_TIANGAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
_DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
_SHENGXIAO = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]
_MONTH_NAMES = ["正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"]
_DAY_NAMES = [
    "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
]
_WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

MIN_YEAR = 1900
MAX_YEAR = 2100


def _leap_month(year: int) -> int:
    """返回该农历年的闰月月份（1-12），无闰月返回 0。"""
    return _LUNAR_INFO[year - 1900] & 0xF


def _leap_days(year: int) -> int:
    """返回该农历年闰月的天数，无闰月返回 0。"""
    if _leap_month(year):
        return 30 if (_LUNAR_INFO[year - 1900] & 0x10000) else 29
    return 0


def _month_days(year: int, month: int) -> int:
    """返回该农历年指定月份（1-12）的天数。"""
    return 30 if (_LUNAR_INFO[year - 1900] & (0x10000 >> month)) else 29


def _year_days(year: int) -> int:
    """返回该农历年的总天数（含闰月）。"""
    total = 348  # 12 个月 * 29 天
    info = _LUNAR_INFO[year - 1900]
    bit = 0x8000
    while bit > 0x8:
        if info & bit:
            total += 1
        bit >>= 1
    return total + _leap_days(year)


def solar_to_lunar(year: int, month: int, day: int) -> dict:
    """
    将公历日期转换为农历日期。

    Args:
        year, month, day: 公历年月日。

    Returns:
        字典，包含字段：
            year    : 农历年（int）
            month   : 农历月（1-12，int）
            day     : 农历日（1-30，int）
            is_leap : 是否为闰月（bool）

    Raises:
        ValueError: 日期超出支持范围（1900-2100）或日期非法。
    """
    if not (MIN_YEAR <= year <= MAX_YEAR):
        raise ValueError(f"仅支持 {MIN_YEAR}-{MAX_YEAR} 年，收到 {year}")

    # 1900-01-31 为农历 1900 年正月初一
    offset = (date(year, month, day) - date(1900, 1, 31)).days
    if offset < 0:
        raise ValueError(f"日期早于支持范围（{MIN_YEAR}-01-31）")

    lunar_year = 1900
    days_of_year = 0
    while lunar_year <= MAX_YEAR and offset > 0:
        days_of_year = _year_days(lunar_year)
        if offset < days_of_year:
            break
        offset -= days_of_year
        lunar_year += 1
    if lunar_year > MAX_YEAR:
        raise ValueError(f"日期超出支持范围（{MAX_YEAR} 年）")

    leap = _leap_month(lunar_year)
    is_leap = False
    lunar_month = 1
    days_of_month = 0
    while lunar_month < 13 and offset > 0:
        if leap > 0 and lunar_month == leap + 1 and not is_leap:
            # 进入闰月（对应经典算法中的 --month）
            lunar_month -= 1
            is_leap = True
            days_of_month = _leap_days(lunar_year)
        else:
            days_of_month = _month_days(lunar_year, lunar_month)
        if is_leap and lunar_month == leap + 1:
            is_leap = False
        offset -= days_of_month
        lunar_month += 1

    # 恰好落在闰月结束边界时的修正
    if offset == 0 and leap > 0 and lunar_month == leap + 1:
        if is_leap:
            is_leap = False
        else:
            is_leap = True
            lunar_month -= 1

    if offset < 0:
        offset += days_of_month
        lunar_month -= 1

    lunar_day = offset + 1
    return {
        'year': lunar_year,
        'month': lunar_month,
        'day': lunar_day,
        'is_leap': is_leap,
    }


def _ganzhi_year(lunar_year: int) -> str:
    """根据农历年计算干支纪年，如 '丙午'。"""
    return _TIANGAN[(lunar_year - 4) % 10] + _DIZHI[(lunar_year - 4) % 12]


def _zodiac(lunar_year: int) -> str:
    """根据农历年计算生肖，如 '马'。"""
    return _SHENGXIAO[(lunar_year - 4) % 12]


def format_lunar(lunar: dict) -> str:
    """
    将 solar_to_lunar 的返回结果格式化为中文描述。

    Returns:
        形如 '丙午年 七月初七' 的字符串（闰月会带 '闰' 前缀）。
    """
    month_text = _MONTH_NAMES[lunar['month'] - 1]
    if lunar['is_leap']:
        month_text = "闰" + month_text
    return f"{_ganzhi_year(lunar['year'])}年 {month_text}月{_DAY_NAMES[lunar['day'] - 1]}"


def get_lunar_info(d: date) -> dict:
    """
    便捷入口：给定公历 date，返回完整的农历信息。

    Returns:
        {'solar': '2026-08-19', 'weekday': '星期三', 'lunar_text': '丙午年 七月初七',
         'zodiac': '马', 'ganzhi': '丙午', 'lunar': {...}}
    """
    lunar = solar_to_lunar(d.year, d.month, d.day)
    return {
        'solar': d.strftime('%Y-%m-%d'),
        'weekday': _WEEKDAY_NAMES[d.weekday()],
        'lunar_text': format_lunar(lunar),
        'zodiac': _zodiac(lunar['year']),
        'ganzhi': _ganzhi_year(lunar['year']),
        'lunar': lunar,
    }
