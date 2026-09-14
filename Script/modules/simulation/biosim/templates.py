"""现成的装配模板 —— 也就是"配置"该待的地方。

参数紧挨着它配置的那个效果写：改一个数字，一眼看得出它改的是谁。
要另一套平衡，照着再写一个模板函数即可（多套预设是函数，不是一袋全局配置）。
"""
from __future__ import annotations

import random

from .EngineClock import EngineClock
from .BasicEffects.physics import EatEffect, ExerciseEffect, SleepEffect, WakeEffect
from .BioEngine import BioEngine


def standard(*, start_hour: float = 8.0, time_step: float = 0.05) -> BioEngine:
    """标准模板：常态生理已挂好、参数写在明面上，拿来就能用。"""
    from .BasicEffects.physiology import (EnergyDynamics, FullnessDynamics, GlucoseDynamics,
                             MoodDynamics, StressDynamics)

    engine = BioEngine(start_hour=start_hour, time_step=time_step)
    engine.add_effects(
        # 常态：清醒静息的基准
        EnergyDynamics(base_cost=2.0, low_glycemia_cost=4.0),
        GlucoseDynamics(decay_per_hour=6.0),
        FullnessDynamics(decay_per_hour=20.0),
        StressDynamics(rise_rate=10.0, fall_rate=5.0, energy_low=40.0, hunger_factor=0.8),
        MoodDynamics(response_rate=2.0, happy_stress=30.0, happy_energy=60.0, irritable_stress=70.0),
    )
    return engine


def standard_with_clock(*, start_hour: float = 8.0, time_step: float = 0.05,
                        update_interval: float = 0.1,
                        time_scale: float = 1.0) -> tuple[BioEngine, EngineClock]:
    """标准模板 + 时钟：按真实时间跑的用法。"""
    engine = standard(start_hour=start_hour, time_step=time_step)
    return engine, EngineClock(engine, update_interval=update_interval, time_scale=time_scale)


def sleep_effect(*, seed: int | None = None, **overrides) -> SleepEffect:
    """睡一觉用的效果：种子注入在这里，不进任何全局配置。"""
    return SleepEffect(rng=random.Random(seed), **overrides)
