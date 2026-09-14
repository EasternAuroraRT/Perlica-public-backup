"""bio_text —— 把 biosim 的类型化读数渲染成文本。

这是【工具】，不是 biosim 的核心功能：
    核心只交出一个 EngineSlice（类型化读数）；
    措辞、词汇、句式、模板全在这里，随便改。
    不想要这一套，就自己写一个 render(engine_slice, actions) -> str ——
    核心对文本一无所知，也不依赖这里任何东西。

占位符（模板里能用到的键，见 fields()）
    {clock} {time} {elapsed_hours} {sleep_duration} {last_sleep_duration}
    {sleep} {activity} {hunger} {mood} {glycemia}                        中文标签
    {sleep_raw} {activity_raw} {hunger_raw} {mood_raw} {glycemia_raw}    原始值
    {energy} {fullness} {mood_score} {stress} {glucose}                  数值
    {energy_label} {fullness_label} {mood_label} {stress_label} {glucose_label}
    {actions}
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable, Literal, Mapping

from modules.simulation.biosim.BioEngine import BioEngine
from modules.simulation.biosim.BioEnum import (ActivityLevel, GlycemiaState, HungerState,
                                              MoodState, SleepState)
from modules.simulation.biosim.EngineSlice import EngineSlice

# 字段名用 Literal：调用处写错字段名，静态检查就抓。
LabelField = Literal["sleep", "activity", "hunger", "mood", "glycemia"]
QualityField = Literal["energy", "fullness", "mood", "stress", "glucose"]

# ---------- 词汇表：改这里就换词；想绕开就用 {xxx_raw} ----------
LABELS: Mapping[LabelField, Mapping[str, str]] = {
    "sleep": {"awake": "清醒", "light_sleep": "浅睡",
              "deep_sleep": "深睡", "rem": "REM"},
    "activity": {"rest": "休息", "moderate": "适度活动", "active": "活跃"},
    "hunger": {"full": "饱食", "content": "不饿", "hungry": "饥饿"},
    "mood": {"neutral": "中性", "happy": "愉悦", "irritable": "易怒"},
    "glycemia": {"low": "血糖偏低", "normal": "血糖正常", "high": "血糖偏高"},
}

QUALITY: Mapping[QualityField, tuple[str, str, str]] = {
    "energy": ("疲惫", "平静", "充沛"),
    "fullness": ("饥饿", "尚可", "饱满"),
    "mood": ("低落", "中性", "愉悦"),
    "stress": ("轻松", "适中", "高压"),
    "glucose": ("偏低", "正常", "偏高"),
}

# 枚举加了成员却忘了翻译，这里立刻炸 —— 而不是渲染时悄悄退化成英文原值。
_TABLES: tuple[tuple[LabelField, type[Enum]], ...] = (
    ("sleep", SleepState), ("activity", ActivityLevel), ("hunger", HungerState),
    ("mood", MoodState), ("glycemia", GlycemiaState),
)
for _field, _enum in _TABLES:
    _missing = sorted(m.value for m in _enum if m.value not in LABELS[_field])
    if _missing:
        raise RuntimeError("LABELS[%r] 缺翻译: %s" % (_field, _missing))

# ---------- 缺省模板 ----------
LINE = (
    "{clock}"
    " | 睡眠:{sleep} 活动:{activity} 饥饿:{hunger} 情绪:{mood}"
    " | 能量:{energy:.0f}/100（{energy_label}）"
    " | 饱腹:{fullness:.0f}/100（{fullness_label}）"
    " | 心情:{mood_score:.0f}/100（{mood_label}）"
    " | 压力:{stress:.0f}/100（{stress_label}）"
    " | 上次睡眠:{last_sleep_duration:.1f}h"
)

# ---------- 渲染 ----------
def _clock(hour: float) -> str:
    """小数小时 -> HH:MM。"""
    whole = int(hour) % 24
    minute = int((hour % 1.0) * 60)
    return "{0:02d}:{1:02d}".format(whole, minute)


def _label(field: LabelField, member: Enum) -> str:
    """枚举 -> 中文标签。表里没有就 KeyError，不悄悄退化成原值。"""
    return LABELS[field][str(member.value)]


def _qual(field: QualityField, value: float) -> str:
    """数值 -> 三档形容（低 / 中 / 高）。"""
    low, mid, high = QUALITY[field]
    if value < 30.0:
        return low
    if value < 70.0:
        return mid
    return high


def fields(engine_slice: EngineSlice, actions: Iterable[str] = ()) -> dict[str, object]:
    """把读数摊平成占位符表（程序也可以直接取用）。"""
    return {
        "clock": _clock(engine_slice.time),
        "time": engine_slice.time,
        "elapsed_hours": engine_slice.elapsed_hours,
        "sleep_duration": engine_slice.sleep_duration,
        "last_sleep_duration": engine_slice.last_sleep_duration,
        "sleep": _label("sleep", engine_slice.sleep),
        "sleep_raw": engine_slice.sleep.value,
        "activity": _label("activity", engine_slice.activity),
        "activity_raw": engine_slice.activity.value,
        "hunger": _label("hunger", engine_slice.hunger),
        "hunger_raw": engine_slice.hunger.value,
        "mood": _label("mood", engine_slice.mood),
        "mood_raw": engine_slice.mood.value,
        "glycemia": _label("glycemia", engine_slice.glycemia),
        "glycemia_raw": engine_slice.glycemia.value,
        "energy": engine_slice.energy,
        "energy_label": _qual("energy", engine_slice.energy),
        "fullness": engine_slice.fullness,
        "fullness_label": _qual("fullness", engine_slice.fullness),
        "mood_score": engine_slice.mood_score,
        "mood_label": _qual("mood", engine_slice.mood_score),
        "stress": engine_slice.stress,
        "stress_label": _qual("stress", engine_slice.stress),
        "glucose": engine_slice.glucose,
        "glucose_label": _qual("glucose", engine_slice.glucose),
        "actions": " / ".join(actions),
    }


def fill(template: str, engine_slice: EngineSlice, actions: Iterable[str] = ()) -> str:
    """用读数填任意模板。"""
    return template.format(**fields(engine_slice, actions))


def state_text(engine: BioEngine, template: str | None = None,
               actions: Iterable[str] = ()) -> str:
    """一行状态摘要。engine 只被用来取读数 —— 核心不认识这里。

    actions 由调用方给：核心没有"动作"这个概念，可做的事是工具层的事。
    """
    return fill(template or LINE, engine.get_slice(), actions)
