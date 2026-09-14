"""引擎：计算器。把当前挂着的效果声明整合成状态变化，别的什么都不管。"""
from __future__ import annotations

import threading

from .Effect import Effect
from .EngineSlice import EngineSlice
from .BasicEffects.physiology import glycemia_of, hunger_of, mood_of
from .types import ActivityLevel, Aspects, BioState, SleepState, StateVec, Tick


class BioEngine:
    """计算器：挂效果、推进时间、交读数。

    - 不认识业务：没有默认变化，常态也是一组普通效果（physiology.BASELINE）。
    - 不认识动作：没有注册表、没有名字派发；要做什么就挂一个效果对象。
    - 不认识配置：效果的数字在它自己的构造参数里，这里只有积分步长。
    - 连续维度的范围、初值、读数档位都写在维度声明上（StateVec.elements），
      所以加一个维度只要动那一个文件。
    """

    def __init__(self, *, start_hour: float = 8.0, time_step: float = 0.05) -> None:
        self.time_step = time_step
        self._dimensions = {d.name: d for d in StateVec.elements}

        initial = StateVec(**{d.name: d.initial for d in StateVec.elements})
        self._bio = BioState(
            initial_state=initial,
            aspects=Aspects(
                sleep=SleepState.AWAKE,
                activity=ActivityLevel.MODERATE,
                stress=0.0,
                clock_hour=start_hour % 24.0,
                elapsed_hours=0.0,
                sleep_duration=0.0,
                last_sleep_duration=0.0,
            ),
        )

        self._effects: list[Effect] = []
        self._tick = Tick()
        self._net = StateVec()
        self._mul = StateVec()
        self._mul.fill(1.0)
        self._lock = threading.RLock()

    # ---------- 挂载 ----------
    def add_effect(self, effect: Effect) -> bool:
        """挂上一个效果：先让它落地（dt=0）。被它自己拒绝就不挂，返回 False。"""
        with self._lock:
            attached = self._attach(effect)
            self._reap(self._tick)
            return attached

    def add_effects(self, *effects: Effect) -> int:
        """一次挂多个，顺序照给。返回真正挂上的个数（被拒的不算）。"""
        with self._lock:
            attached = sum(1 for effect in effects if self._attach(effect))
            self._reap(self._tick)
            return attached

    def _attach(self, effect: Effect) -> bool:
        """让一个效果落地，但不回收 —— 由调用方在同一把锁里统一回收一遍。"""
        observation = self.get_slice()
        if effect.refusal(observation) is not None:
            return False
        self._tick.set(0.0, observation.time, observation.elapsed_hours)
        self._apply(effect.influence(self._bio, self._tick))
        self._effects.append(effect)
        return True

    def remove_effect(self, effect: Effect) -> bool:
        """摘掉一个效果（收尾照走）。不在列表里就什么都不做。"""
        with self._lock:
            if effect not in self._effects:
                return False
            self._effects.remove(effect)
            a = self._bio.aspects
            self._tick.set(0.0, a.clock_hour, a.elapsed_hours)
            effect.on_expire(self._bio, self._tick)
            return True

    @property
    def effects(self) -> tuple[Effect, ...]:
        """当前挂着的效果（只读快照）。"""
        with self._lock:
            return tuple(self._effects)

    # ---------- 读数 ----------
    def get_slice(self) -> EngineSlice:
        """类型化读模型：核心只交数据，怎么措辞是消费方的事。"""
        with self._lock:
            st = self._bio.current_state
            a = self._bio.aspects
            return EngineSlice(
                sleep=a.sleep,
                activity=a.activity,
                hunger=hunger_of(st.fullness),
                mood=mood_of(st.mood),
                glycemia=glycemia_of(st.glucose),
                energy=st.energy,
                fullness=st.fullness,
                mood_score=st.mood,
                glucose=st.glucose,
                stress=a.stress,
                time=a.clock_hour,
                elapsed_hours=a.elapsed_hours,
                sleep_duration=a.sleep_duration,
                last_sleep_duration=a.last_sleep_duration,
            )

    # ---------- 计算 ----------
    def advance(self, hours: float) -> None:
        """推进模拟时间（内部按 time_step 分步）。"""
        remaining = hours
        while remaining > 0:
            delta = min(self.time_step, remaining)
            with self._lock:
                self._step(delta)
            remaining -= delta

    # ---------- 内部 ----------
    def _step(self, dt: float) -> None:
        tick = self._tick
        a = self._bio.aspects
        tick.set(dt, a.clock_hour, a.elapsed_hours)

        net = self._net
        mul = self._mul
        net.zero()
        mul.fill(1.0)                       # 乘区单位元：没人声明就是 1.0
        for eff in self._effects:
            inf = eff.influence(self._bio, tick)
            net += inf.delta                    # 基础值：求和
            mul.multiply_by_shifted(inf.mul)    # 乘区：并进 (1 + 提交倍率)
            inf.aspects.apply_to(a)             # 方面：只覆盖声明里给了的字段

        net *= mul                          # 实际修改 = 基础值之和 x 乘区倍率
        self._bio.current_state.add_scaled(net, tick.dt)
        self._clamp_state()

        a.clock_hour = (a.clock_hour + dt) % 24.0
        a.elapsed_hours = a.elapsed_hours + dt
        self._reap(tick)

    def _apply(self, inf) -> None:
        """落地一个效果的瞬时声明（挂载那一刻，dt=0）。"""
        inf.aspects.apply_to(self._bio.aspects)
        self._bio.current_state += inf.instant
        inf.instant.zero()
        self._clamp_state()

    def _reap(self, tick: Tick) -> None:
        kept = []
        for eff in self._effects:
            if eff.alive(self._bio, tick):
                kept.append(eff)
            else:
                eff.on_expire(self._bio, tick)
        self._effects = kept

    def _clamp_state(self) -> None:
        s = self._bio.current_state
        for name, dim in self._dimensions.items():
            setattr(s, name, dim.clamp(getattr(s, name)))
