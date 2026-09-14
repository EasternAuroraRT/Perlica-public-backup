"""观察：程序侧的类型化读模型。

LLM 看的那份是文本（get_state_text）；程序自己判断的那份是这个 ——
字段带类型、可静态检查、加字段不改接口。
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
    # 时钟
    time: float
    elapsed_hours: float
    sleep_duration: float
    last_sleep_duration: float

    def __str__(self) -> str:
        result: str = ""
        for attr in dir(self):
            if not attr.startswith('__'):
                result += f"{attr}: {getattr(self, attr)}\n"
        return result
