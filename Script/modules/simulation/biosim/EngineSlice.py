"""观察：程序侧的类型化读模型。

LLM 看的那份是文本（get_state_text）；程序自己判断的那份是这个 ——
字段带类型、可静态检查、加字段不改接口。

单位约定：clock_hour / elapsed_hours / sleep_duration_hours /
last_sleep_duration_hours 均为模拟小时；energy / fullness / mood_score /
stress / glucose 为 0~100 的百分比刻度。
"""
from __future__ import annotations

from dataclasses import dataclass

from .types import SleepState, ActivityLevel, GlycemiaState, HungerState, MoodState


@dataclass(frozen=True)
class EngineSlice:
    # 离散相位（枚举）
    sleep: SleepState
    activity: ActivityLevel
    hunger: HungerState
    mood: MoodState
    glycemia: GlycemiaState
    # 连续指标（0~100）
    energy: float
    fullness: float
    mood_score: float
    stress: float
    glucose: float
    # 时钟（小时）
    clock_hour: float
    elapsed_hours: float
    sleep_duration_hours: float
    last_sleep_duration_hours: float

    def __str__(self) -> str:
        result: str = ""
        for attr in dir(self):
            if not attr.startswith('__'):
                result += f"{attr}: {getattr(self, attr)}\n"
        return result
