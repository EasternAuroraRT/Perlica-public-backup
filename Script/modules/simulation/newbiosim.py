"""
newbiosim — 生物节律连续状态机（重建于 inner 向量类型之上）。

设计要点
--------
* 状态不再是一组离散枚举，而是一个「规格化向量」：StateVec 的每个分量
  就是一个独立状态变量（energy / fullness / mood）。
* 状态转移 = 向量在相空间里的连续移动。引擎每步计算一个「每模拟小时变化率」
  向量（delta_per_hour），把它写入 BioState.delta_per_sec，再对 current_state
  做一次积分。
* 离散的相位信息（睡眠阶段、活动强度、压力）单独维护，作为驱动连续维度的“规则层”。
* 主动行为（eat / exercise / sleep / wake）只修改状态与相位，不触碰引擎核心。

对外暴露的公共符号只有：BioSimConfig、四个状态枚举和 BioSimEngine。
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from enum import Enum

from inner import BioState, StateVec


# --- 状态枚举（相位层） -----------------------------------------------------

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


# --- 配置 --------------------------------------------------------------------

@dataclass
class BioSimConfig:
    """生物节律全部可调参数。时间单位统一为小时，能量/饱腹/心情取 0~100。"""

    # 睡眠
    sleep_full_duration: float = 8.0        # 睡足所需小时数
    doze_timeout: float = 2.0               # 赖床后自动清醒等待小时数

    # 能量
    energy_awake_cost: float = 2.0          # 清醒每小时消耗
    energy_sleep_recover: float = 5.0       # 睡眠每小时恢复
    energy_doze_recover: float = 1.0        # 赖床每小时恢复
    energy_max: float = 100.0
    energy_min: float = 0.0
    energy_eat_gain: float = 10.0           # 进食一次恢复能量
    energy_exercise_cost_base: float = 15.0 # 运动基础消耗（乘以强度）
    energy_low_threshold: float = 30.0      # 低于此值倾向休息

    # 饱腹（饥饿的连续化表示）
    fullness_max: float = 100.0
    fullness_min: float = 0.0
    fullness_decay_per_hour: float = 20.0   # 每小时饥饿衰减
    fullness_full_threshold: float = 60.0   # >= 此值视为 FULL
    fullness_content_threshold: float = 25.0# >= 此值视为 CONTENT，否则 HUNGRY

    # 活动
    exercise_cooldown: float = 1.0          # 运动后保持 ACTIVE 的时长

    # 压力
    stress_rise_rate: float = 10.0          # 基础压力上升速率
    stress_fall_rate: float = 5.0           # 基础压力下降速率
    stress_irritable_threshold: float = 70.0
    stress_happy_threshold: float = 30.0
    stress_happy_energy_required: float = 60.0
    stress_energy_low_threshold: float = 40.0
    stress_hunger_factor: float = 0.8
    stress_doze_factor: float = 0.3

    # 心情（连续值，随压力/能量漂移）
    mood_max: float = 100.0
    mood_min: float = 0.0
    mood_response_rate: float = 2.0         # 心情向目标值趋近的每小时速率
    mood_happy_threshold: float = 70.0      # >= 此值视为 HAPPY
    mood_irritable_threshold: float = 30.0  # <= 此值视为 IRRITABLE

    # 模拟
    time_step: float = 0.05                 # 内部积分步长（小时）


# --- 引擎主体 -----------------------------------------------------------------

class BioSimEngine:
    """多线程生物节律连续状态机引擎。

    后台线程按 update_interval 采样，用单调时钟实测真实经过时间，按照
    time_scale（模拟时间 / 现实时间）推进模拟，理论零累计误差。主动接口
    与查询接口均加锁，可在任意线程安全调用。
    """

    def __init__(
        self,
        config: BioSimConfig | None = None,
        start_hour: float = 8.0,
        update_interval: float = 0.1,
        time_scale: float = 1.0,
    ) -> None:
        self.cfg = config if config is not None else BioSimConfig()
        self.update_interval = update_interval
        self.time_scale = time_scale

        # 连续状态：当前向量 + 每模拟秒变化率。
        energy = self.cfg.energy_max * 0.8
        self._bio = BioState(
            initial_state=StateVec(energy=energy, fullness=self.cfg.fullness_max, mood=50.0),
        )

        # 相位层
        self._sleep = SleepState.AWAKE
        self._activity = ActivityLevel.MODERATE
        self._stress = 0.0
        self._sleep_duration = 0.0
        self._doze_timer = 0.0
        self._exercise_timer = 0.0

        # 时钟
        self._clock_hour = start_hour % 24.0
        self._elapsed_hours = 0.0

        # 线程/时间基准
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._base_sim_hours = 0.0
        self._segment_start = time.monotonic()
        self._last_sim_hours = 0.0

    # ---------- 状态查询 ----------
    @property
    def current(self) -> StateVec:
        """当前状态向量（防御性拷贝，不会泄露内部引用）。"""
        with self._lock:
            s = self._bio.current_state
            return StateVec(energy=s.energy, fullness=s.fullness, mood=s.mood)

    def snapshot(self) -> dict:
        """返回当前状态的快照。枚举成员可直接用 .value 读取其字符串值。"""
        with self._lock:
            state = self._bio.current_state
            return {
                "time": self._clock_hour,
                "elapsed_hours": self._elapsed_hours,
                "sleep": self._sleep,
                "activity": self._activity,
                "hunger": self._hunger_state(),
                "mood": self._mood_state(),
                "energy": state.energy,
                "fullness": state.fullness,
                "mood_score": state.mood,
                "stress": self._stress,
                "sleep_duration": self._sleep_duration,
            }

    # ---------- 主动干预 ----------
    def eat(self) -> None:
        with self._lock:
            energy = min(self.cfg.energy_max,
                         self._bio.current_state.energy + self.cfg.energy_eat_gain)
            self._bio.current_state = StateVec(
                energy=energy,
                fullness=self.cfg.fullness_max,
                mood=self._bio.current_state.mood,
            )

    def exercise(self, intensity: float = 1.0) -> None:
        with self._lock:
            if self._sleep != SleepState.AWAKE:
                raise RuntimeError("cannot exercise while asleep")
            energy = max(self.cfg.energy_min,
                         self._bio.current_state.energy - self.cfg.energy_exercise_cost_base * intensity)
            self._bio.current_state = StateVec(
                energy=energy,
                fullness=self._bio.current_state.fullness,
                mood=self._bio.current_state.mood,
            )
            self._activity = ActivityLevel.ACTIVE
            self._exercise_timer = self.cfg.exercise_cooldown

    def sleep(self) -> None:
        with self._lock:
            if self._sleep in (SleepState.AWAKE, SleepState.DOZING):
                self._sleep = SleepState.LIGHT
                self._sleep_duration = 0.0
                self._doze_timer = 0.0
                self._exercise_timer = 0.0

    def wake(self) -> None:
        with self._lock:
            if self._sleep != SleepState.AWAKE:
                self._sleep = SleepState.AWAKE
                self._sleep_duration = 0.0
                self._doze_timer = 0.0

    # ---------- 手动推进（供测试/前台使用） ----------
    def advance(self, hours: float) -> None:
        """手动推进若干小时（自动按 time_step 子步积分，线程安全）。"""
        step = self.cfg.time_step
        remaining = hours
        while remaining > 0:
            delta = min(step, remaining)
            with self._lock:
                self._update(delta)
            remaining -= delta

    # ---------- 线程控制 ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        with self._lock:
            self._base_sim_hours = 0.0
            self._segment_start = time.monotonic()
            self._last_sim_hours = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        with self._lock:
            target = self._sim_hours_now()
            delta = target - self._last_sim_hours
            if delta > 0:
                self.advance(delta)
                self._last_sim_hours = target

    def set_time_scale(self, scale: float) -> None:
        """动态调整时间倍率（模拟 / 现实）。"""
        with self._lock:
            scale = max(0.0, scale)
            self._base_sim_hours = self._sim_hours_now()
            self._segment_start = time.monotonic()
            self.time_scale = scale

    # ---------- 内部：时间基准 ----------
    def _sim_hours_now(self) -> float:
        return self._base_sim_hours + (time.monotonic() - self._segment_start) * self.time_scale / 3600.0

    def _run(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self.update_interval)
            if self._stop_event.is_set():
                break
            with self._lock:
                target = self._sim_hours_now()
                delta = target - self._last_sim_hours
                if delta > 0:
                    self.advance(delta)
                    self._last_sim_hours = target

    # ---------- 内部：规则层 ----------
    def _update(self, dt: float) -> None:
        # 相位层先更新（睡眠决定能量恢复速率，活动/压力依赖状态），
        # 再用更新后的相位计算连续维度的变化率。
        self._update_sleep(dt)
        self._update_activity(dt)
        self._update_stress(dt)

        delta_hour = self._compute_delta_per_hour()
        self._bio.delta_per_sec = delta_hour / 3600.0
        self._bio.current_state = self._bio.current_state + delta_hour * dt
        self._clamp_state()

        self._clock_hour = (self._clock_hour + dt) % 24.0
        self._elapsed_hours += dt

    def _compute_delta_per_hour(self) -> StateVec:
        state = self._bio.current_state
        cfg = self.cfg

        if self._sleep == SleepState.AWAKE:
            d_energy = -cfg.energy_awake_cost
        elif self._sleep == SleepState.DOZING:
            d_energy = cfg.energy_doze_recover
        else:
            d_energy = cfg.energy_sleep_recover

        d_fullness = -cfg.fullness_decay_per_hour

        target_mood = 100.0 - self._stress
        if self._stress <= cfg.stress_happy_threshold and state.energy >= cfg.stress_happy_energy_required:
            target_mood = max(target_mood, 85.0)
        elif self._stress >= cfg.stress_irritable_threshold:
            target_mood = min(target_mood, 20.0)
        target_mood = min(cfg.mood_max, max(cfg.mood_min, target_mood))
        d_mood = (target_mood - state.mood) * cfg.mood_response_rate

        return StateVec(energy=d_energy, fullness=d_fullness, mood=d_mood)

    def _clamp_state(self) -> None:
        s = self._bio.current_state
        s.energy = min(self.cfg.energy_max, max(self.cfg.energy_min, s.energy))
        s.fullness = min(self.cfg.fullness_max, max(self.cfg.fullness_min, s.fullness))
        s.mood = min(self.cfg.mood_max, max(self.cfg.mood_min, s.mood))

    def _update_sleep(self, dt: float) -> None:
        if self._sleep in (SleepState.LIGHT, SleepState.DEEP, SleepState.REM):
            self._sleep_duration += dt
            if self._sleep_duration >= self.cfg.sleep_full_duration:
                self._sleep = SleepState.DOZING
                self._doze_timer = 0.0
            else:
                cycle_pos = (self._sleep_duration % 1.5) / 1.5
                if cycle_pos < 0.2:
                    self._sleep = SleepState.LIGHT
                elif cycle_pos < 0.6:
                    self._sleep = SleepState.DEEP
                else:
                    self._sleep = SleepState.REM
        elif self._sleep == SleepState.DOZING:
            self._doze_timer += dt
            self._sleep_duration += dt
            if self._doze_timer >= self.cfg.doze_timeout:
                self._sleep = SleepState.AWAKE
                self._sleep_duration = 0.0
                self._doze_timer = 0.0

    def _update_activity(self, dt: float) -> None:
        if self._sleep != SleepState.AWAKE:
            self._activity = ActivityLevel.REST
            self._exercise_timer = 0.0
            return
        if self._exercise_timer > 0:
            self._exercise_timer -= dt
            if self._exercise_timer <= 0:
                self._activity = ActivityLevel.MODERATE
        else:
            self._activity = (
                ActivityLevel.REST
                if self._bio.current_state.energy < self.cfg.energy_low_threshold
                else ActivityLevel.MODERATE
            )

    def _update_stress(self, dt: float) -> None:
        cfg = self.cfg
        energy = self._bio.current_state.energy
        hunger = self._hunger_state()

        gain = 0.0
        if energy < cfg.stress_energy_low_threshold:
            ratio = (cfg.stress_energy_low_threshold - energy) / cfg.stress_energy_low_threshold
            gain += cfg.stress_rise_rate * ratio
        if hunger == HungerState.HUNGRY:
            gain += cfg.stress_rise_rate * cfg.stress_hunger_factor
        if self._sleep == SleepState.DOZING:
            gain += cfg.stress_rise_rate * cfg.stress_doze_factor

        loss = 0.0
        if energy > cfg.stress_energy_low_threshold and hunger != HungerState.HUNGRY:
            loss = cfg.stress_fall_rate
        elif energy > cfg.stress_energy_low_threshold * 0.5 and hunger == HungerState.CONTENT:
            loss = cfg.stress_fall_rate * 0.5

        self._stress = min(100.0, max(0.0, self._stress + (gain - loss) * dt))

    def _hunger_state(self) -> HungerState:
        full = self._bio.current_state.fullness
        if full >= self.cfg.fullness_full_threshold:
            return HungerState.FULL
        if full >= self.cfg.fullness_content_threshold:
            return HungerState.CONTENT
        return HungerState.HUNGRY

    def _mood_state(self) -> MoodState:
        m = self._bio.current_state.mood
        if m >= self.cfg.mood_happy_threshold:
            return MoodState.HAPPY
        if m <= self.cfg.mood_irritable_threshold:
            return MoodState.IRRITABLE
        return MoodState.NEUTRAL


if __name__ == "__main__":
    import json

    fast = BioSimConfig(
        sleep_full_duration=3.0,
        doze_timeout=0.2,
        energy_awake_cost=8.0,
        energy_sleep_recover=15.0,
        fullness_decay_per_hour=40.0,
        exercise_cooldown=0.4,
        stress_rise_rate=30.0,
        stress_fall_rate=8.0,
        stress_irritable_threshold=60.0,
        stress_happy_threshold=25.0,
        stress_happy_energy_required=50.0,
        stress_energy_low_threshold=50.0,
        time_step=0.05,
    )
    engine = BioSimEngine(config=fast, start_hour=8.0, update_interval=0.5, time_scale=360)
    engine.start()
    try:
        for i in range(8):
            time.sleep(1.0)
            if i == 1:
                engine.eat()
            if i == 3:
                engine.exercise(1.0)
            if i == 5:
                engine.sleep()
            snap = engine.snapshot()
            print(json.dumps({k: (v.value if hasattr(v, "value") else round(v, 2)) for k, v in snap.items()},
                             ensure_ascii=False))
    finally:
        engine.stop()

    manual = BioSimEngine(config=fast, start_hour=8.0)
    manual.advance(4.0)
    print("manual snapshot:", manual.snapshot())

