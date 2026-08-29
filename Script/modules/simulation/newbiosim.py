# Not finished yet

import enum
import time
import json
import threading
from dataclasses import dataclass
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]


# ---------- 单位标注（仅类型层面，运行时仍为 float） ----------
Hours = Annotated[float, "h"]                   # 小时
Energy = Annotated[float, "energy"]             # 能量值（0 ~ 100）
EnergyPerHour = Annotated[float, "energy/h"]    # 能量变化速率
Stress = Annotated[float, "stress"]             # 压力值（0 ~ 100）
StressRate = Annotated[float, "stress/h"]       # 压力变化速率
Factor = Annotated[float, "x"]                  # 无量纲倍率


# ==================== 配置类 ====================
@dataclass
class BiorythmConfig:
    """生物节律状态机全部可调参数（字段类型注解带单位，可用 field_units() 查询）"""

    # ---------- 睡眠 ----------
    sleep_full_duration: Hours = 8.0            # 睡足所需小时数
    doze_timeout: Hours = 2.0                   # 赖床后自动清醒等待时间

    # ---------- 能量 ----------
    energy_awake_cost: EnergyPerHour = 2.0         # 清醒每小时消耗
    energy_sleep_recover: EnergyPerHour = 5.0      # 睡眠每小时恢复
    energy_doze_recover: EnergyPerHour = 1.0       # 赖床每小时恢复
    energy_max: Energy = 100.0
    energy_min: Energy = 0.0
    energy_eat_gain: Energy = 10.0              # 进食恢复能量
    energy_exercise_cost_base: Energy = 15.0    # 运动基础消耗（倍乘强度）
    energy_low_threshold: Energy = 30.0         # 低于此值倾向休息

    # ---------- 饥饿 ----------
    hunger_full_duration: Hours = 1.0           # FULL 持续小时数
    hunger_content_duration: Hours = 4.0        # CONTENT 持续小时数

    # ---------- 活动 ----------
    exercise_cooldown: Hours = 1.0              # 运动后保持 ACTIVE 的时长

    # ---------- 压力 ----------
    stress_rise_rate: StressRate = 10.0         # 基础压力上升速率
    stress_fall_rate: StressRate = 5.0          # 基础压力下降速率
    stress_irritable_threshold: Stress = 70.0   # 压力超过此值为 irritable
    stress_happy_threshold: Stress = 30.0       # 压力低于此值且能量高时为 happy
    stress_happy_energy_required: Energy = 60.0
    stress_energy_low_threshold: Energy = 40.0  # 能量低于此值开始升压
    stress_hunger_factor: Factor = 0.8          # 饥饿状态附加压力系数
    stress_doze_factor: Factor = 0.3            # 赖床附加压力系数
    stress_mood_recovery_neutral: Stress = 50.0
    stress_mood_recovery_happy: Stress = 40.0

    # ---------- 模拟精度 ----------
    time_step: Hours = 0.05                     # 内部更新步长

    def to_json_file(self, filepath: str):
        with open(filepath, 'w') as f:
            json.dump(self.__dict__, f, indent=4)

    @classmethod
    def from_json_file(cls, filepath: str) -> 'BiorythmConfig':
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls(**data)

    @classmethod
    def field_units(cls) -> dict[str, str]:
        """返回 字段名 -> 单位 的映射（提取自类型注解）"""
        units: dict[str, str] = {}
        for name, hint in get_type_hints(cls, include_extras=True).items():
            metadata = getattr(hint, "__metadata__", None)
            if metadata:
                units[name] = metadata[0]
        return units


# ==================== 状态枚举 ====================
class SleepState(enum.Enum):
    AWAKE = "awake"
    DOZING = "dozing"
    LIGHT_SLEEP = "light_sleep"
    DEEP_SLEEP = "deep_sleep"
    REM = "rem"

class ActivityLevel(enum.Enum):
    REST = "rest"
    MODERATE = "moderate"
    ACTIVE = "active"

class HungerState(enum.Enum):
    FULL = "full"
    CONTENT = "content"
    HUNGRY = "hungry"

class MoodState(enum.Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    IRRITABLE = "irritable"