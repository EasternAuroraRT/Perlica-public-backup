"""配置层：全部可调参数 + 工厂方法。"""
from dataclasses import dataclass


@dataclass
class BioSimConfig:
    """全部可调参数。

    连续维度的约定：每个维度提供 "<名>_initial / <名>_min / <名>_max" 三个值，
    引擎只按这个约定取用，不认识具体维度 —— 加一个维度不用动引擎。
    """
    # 睡眠
    sleep_full_duration: float = 8.0
    doze_timeout: float = 2.0
    # 能量
    energy_awake_cost: float = 2.0
    energy_sleep_recover: float = 5.0
    energy_doze_recover: float = 1.0
    energy_max: float = 100.0
    energy_initial: float = 80.0
    energy_min: float = 0.0
    energy_low_threshold: float = 30.0
    # 饱腹（胃里装了多少；下降速度由血糖浓度决定，不是固定斜率）
    fullness_max: float = 100.0
    fullness_initial: float = 100.0
    fullness_min: float = 0.0
    fullness_decay_per_hour: float = 20.0
    fullness_decay_high_factor: float = 0.35
    fullness_decay_low_factor: float = 1.60
    fullness_full_threshold: float = 60.0
    fullness_content_threshold: float = 25.0
    # 血糖
    glucose_max: float = 100.0
    glucose_initial: float = 55.0
    glucose_min: float = 0.0
    glucose_decay_per_hour: float = 6.0
    glucose_sleep_decay_factor: float = 0.40
    glucose_decay_high_factor: float = 1.30
    glucose_decay_low_factor: float = 0.50
    glucose_high_threshold: float = 62.0
    glucose_low_threshold: float = 38.0
    energy_low_glycemia_cost: float = 4.0
    # 压力
    stress_rise_rate: float = 10.0
    stress_fall_rate: float = 5.0
    stress_irritable_threshold: float = 70.0
    stress_happy_threshold: float = 30.0
    stress_happy_energy_required: float = 60.0
    stress_energy_low_threshold: float = 40.0
    stress_hunger_factor: float = 0.8
    stress_doze_factor: float = 0.3
    # 心情
    mood_max: float = 100.0
    mood_initial: float = 50.0
    mood_min: float = 0.0
    mood_response_rate: float = 2.0
    mood_happy_threshold: float = 70.0
    mood_irritable_threshold: float = 30.0
    # 活动
    exercise_cooldown: float = 1.0
    # 进食：饱腹是立刻的（胃装满了），血糖/能量/心情才随消化慢慢上来
    digest_duration: float = 1.0
    digest_fullness_gain: float = 60.0
    digest_glucose_gain: float = 70.0
    digest_energy_gain: float = 8.0
    digest_mood_gain: float = 12.0
    # 运动
    exercise_energy_rate: float = 4.0
    exercise_glucose_rate: float = 12.0
    # 模拟
    time_step: float = 0.05


def default_config(**overrides) -> BioSimConfig:
    return BioSimConfig(**overrides)

