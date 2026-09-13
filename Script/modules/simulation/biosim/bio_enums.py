"""inner.bio_enums — 生物状态相关的枚举（供 BioState 键入与客户端复用）。"""

from enum import Enum


class SleepState(Enum):
    AWAKE = "awake"
    DOZING = "dozing"
    LIGHT = "light_sleep"
    DEEP = "deep_sleep"
    REM = "rem"


class ActivityLevel(Enum):
    REST = "rest"
    MODERATE = "moderate"
    ACTIVE = "active"


class HungerState(Enum):
    FULL = "full"
    CONTENT = "content"
    HUNGRY = "hungry"


class MoodState(Enum):
    IRRITABLE = "irritable"
    NEUTRAL = "neutral"
    HAPPY = "happy"


class GlycemiaState(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"

