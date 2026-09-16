"""引擎：计算器 + 时间所有者。

- 只做积分：效果声明 → 引擎整合 → 唯一改状态的地方。
- 时间也归它：持有 `integration_step_hours`（精度）与 `sim_hours_per_real_hour`（缩放），
  在读取 / 改状态 / 存档前把模拟同步到"现在"。真实时间是唯一真相，没有独立的时钟对象。
- 不认识业务、不认识动作、不知道措辞。
"""
from __future__ import annotations

import os
import pickle
import threading
import time
from datetime import datetime
from pathlib import Path

from .Effect import Effect
from .EngineSlice import EngineSlice
from .BasicEffects.physiology import glycemia_of, hunger_of, mood_of
from .types import ActivityLevel, Aspects, BioState, SleepState, StateVec, Tick


class BioEngine:
    """计算器 + 时间所有者：挂效果、按真实时间推进、交读数。

    概念上只有两个模拟参数：
        integration_step_hours    积分精度（把区间切多细）
        sim_hours_per_real_hour   模拟缩放（模拟小时 / 真实小时）
    推进一步时，`_step` 收到的 dt 就是实际推进的模拟时长（整步 = 精度，末步 = 零头），
    效果器据此计算；没有人需要关心"多久调一次"。
    """

    def __init__(self, *, start_clock_hour: float = 8.0, integration_step_hours: float = 0.05,
                 sim_hours_per_real_hour: float = 1.0,
                 checkpoint: str | Path | None = None) -> None:
        if integration_step_hours <= 0:
            raise ValueError("integration_step_hours 必须为正数")
        self.integration_step_hours = integration_step_hours
        self.sim_hours_per_real_hour = max(0.0, sim_hours_per_real_hour)
        self._dimensions = {d.name: d for d in StateVec.elements}

        initial = StateVec(**{d.name: d.initial for d in StateVec.elements})
        self._bio = BioState(
            initial_state=initial,
            aspects=Aspects(
                sleep=SleepState.AWAKE,
                activity=ActivityLevel.MODERATE,
                stress=0.0,
                clock_hour=start_clock_hour % 24.0,
                elapsed_hours=0.0,
                sleep_duration_hours=0.0,
                last_sleep_duration_hours=0.0,
                digest_left_hours=0.0,
                exercise_left_hours=0.0,
            ),
        )

        self._effects: list[Effect] = []
        self._tick = Tick()
        self._net = StateVec()
        self._mul = StateVec()
        self._mul.fill(1.0)
        self._lock = threading.RLock()
        self._last_sync_unix_seconds = time.time()

        self.loaded_from_checkpoint = False

        self._checkpoint_dir = Path(checkpoint) if checkpoint is not None else None
        if self._checkpoint_dir is not None:
            self.loaded_from_checkpoint = self.load_checkpoint(self._checkpoint_dir)
        self._last_sync_unix_seconds = time.time()

    # ---------- 时间 ----------
    def sync(self) -> None:
        """把模拟同步到此刻（读取 / 改状态 / 存档前都会自动做）。"""
        with self._lock:
            self._sync_locked()

    def _sync_locked(self) -> None:
        """按挂钟把模拟推到此刻。调用方需持有锁。"""
        now = time.time()
        due_hours = (now - self._last_sync_unix_seconds) * self.sim_hours_per_real_hour / 3600.0
        self._last_sync_unix_seconds = now
        if due_hours > 0.0:
            self.advance(due_hours)      # RLock 可重入

    def set_sim_hours_per_real_hour(self, sim_hours_per_real_hour: float) -> None:
        with self._lock:
            self._sync_locked()          # 先按旧倍率结算，再改
            self.sim_hours_per_real_hour = max(0.0, sim_hours_per_real_hour)

    # ---------- 存档 ----------
    CHECKPOINT_FILE = "checkpoint.pkl"
    CHECKPOINT_FORMAT = 1

    def save_checkpoint(self, dirpath: str | Path | None = None) -> Path:
        """同步到此刻 → 在锁内取切片 → 锁外落盘（IO 不占锁）。"""
        directory = Path(dirpath) if dirpath is not None else self._checkpoint_dir
        if directory is None:
            raise ValueError("没有存档路径：要么传参数，要么构造时给 checkpoint")
        with self._lock:
            self._sync_locked()
            payload = {
                "format": self.CHECKPOINT_FORMAT,
                "state": self._bio,
                "effects": list(self._effects),
                "saved_at_unix_seconds": self._last_sync_unix_seconds,
                "sim_hours_per_real_hour": self.sim_hours_per_real_hour,
            }
            blob = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)

        # 原子写：先写 .tmp 再替换 —— 读半个存档比读不到更糟
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / self.CHECKPOINT_FILE
        temp = target.with_name(target.name + ".tmp")
        with open(temp, "wb") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        return target

    def load_checkpoint(self, dirpath: str | Path | None = None) -> bool:
        """从目录恢复。**那里没有存档就当新的一天**（返回 False），不是错误。

        文件在、却读不了则照抛（UnpicklingError / ValueError）—— 那是异常，
        不该被当成"还没有存档"悄悄吞掉。

        跨天（存档日期 != 今天）同样不恢复、当新的一天（也返回 False）——
        外界事件不该被"补"出来。同日则把停机那段按倍率补算进状态。
        """
        directory = Path(dirpath) if dirpath is not None else self._checkpoint_dir
        if directory is None:
            raise ValueError("没有存档路径：要么传参数，要么构造时给 checkpoint")
        target = directory / self.CHECKPOINT_FILE
        if not target.exists():
            return False
        with open(target, "rb") as handle:
            payload = pickle.load(handle)
        if not isinstance(payload, dict) or payload.get("format") != self.CHECKPOINT_FORMAT:
            raise ValueError("存档格式不认识: %s" % target)

        # 兼容旧键名：saved_at / time_scale
        gap_hours = 0.0
        saved_at_unix_seconds = payload.get("saved_at_unix_seconds", payload.get("saved_at"))
        if saved_at_unix_seconds is not None:
            saved_dt = datetime.fromtimestamp(float(saved_at_unix_seconds))
            now_dt = datetime.now()
            if saved_dt.date() != now_dt.date():
                return False
            self.sim_hours_per_real_hour = float(
                payload.get("sim_hours_per_real_hour", payload.get("time_scale", 1.0))
            )
            gap_hours = max(
                0.0,
                (now_dt - saved_dt).total_seconds() / 3600.0 * self.sim_hours_per_real_hour,
            )

        with self._lock:
            self._bio = payload["state"]
            self._effects = list(payload["effects"])

        if gap_hours > 0:
            self.advance(gap_hours)
        return True

    # ---------- 挂载 ----------
    def add_effect(self, effect: Effect) -> bool:
        """挂上一个效果：先让它落地（dt=0）。被它自己拒绝就不挂，返回 False。"""
        with self._lock:
            self._sync_locked()
            attached = self._attach(effect)
            self._reap(self._tick)
            return attached

    def add_effects(self, *effects: Effect) -> int:
        """一次挂多个，顺序照给。返回真正挂上的个数（被拒的不算）。"""
        with self._lock:
            self._sync_locked()
            attached = sum(1 for effect in effects if self._attach(effect))
            self._reap(self._tick)
            return attached

    def _attach(self, effect: Effect) -> bool:
        """让一个效果落地，但不回收 —— 由调用方在同一把锁里统一回收一遍。"""
        observation = self._slice_locked()
        if effect.refusal(observation) is not None:
            return False
        self._tick.set(0.0, observation.clock_hour, observation.elapsed_hours)
        self._apply(effect.influence(self._bio, self._tick))
        self._effects.append(effect)
        return True

    def remove_effect(self, effect: Effect) -> bool:
        """摘掉一个效果。不在列表里就什么都不做。"""
        with self._lock:
            self._sync_locked()
            if effect not in self._effects:
                return False
            self._effects.remove(effect)
            return True

    @property
    def effects(self) -> tuple[Effect, ...]:
        """当前挂着的效果（只读快照）。"""
        with self._lock:
            return tuple(self._effects)

    @property
    def elapsed_hours(self) -> float:
        """累计模拟小时（含同步到此刻的部分）。"""
        self.sync()
        with self._lock:
            return self._bio.aspects.elapsed_hours

    # ---------- 读数 ----------
    def get_slice(self) -> EngineSlice:
        """类型化读模型：先同步到此刻，再交数据。"""
        with self._lock:
            self._sync_locked()
            return self._slice_locked()

    def _slice_locked(self) -> EngineSlice:
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
            clock_hour=a.clock_hour,
            elapsed_hours=a.elapsed_hours,
            sleep_duration_hours=a.sleep_duration_hours,
            last_sleep_duration_hours=a.last_sleep_duration_hours,
        )

    # ---------- 计算 ----------
    def advance(self, sim_hours: float) -> None:
        """把模拟时间推进 sim_hours：按 integration_step_hours 走整步，零头结算。

        `_step` 拿到的 dt 就是**实际推进的模拟时长**；效果器据此计算。
        """
        step = self.integration_step_hours
        remaining_hours = sim_hours
        with self._lock:
            while remaining_hours > 1e-12:
                dt_hours = step if remaining_hours >= step else remaining_hours
                self._step(dt_hours)
                remaining_hours -= dt_hours

    # ---------- 内部 ----------
    def _step(self, dt_hours: float) -> None:
        tick = self._tick
        a = self._bio.aspects
        tick.set(dt_hours, a.clock_hour, a.elapsed_hours)

        net = self._net
        mul = self._mul
        net.zero()
        mul.fill(1.0)                       # 乘区单位元：没人声明就是 1.0
        for eff in self._effects:
            inf = eff.influence(self._bio, tick)
            net += inf.delta_per_hour           # 基础值（每小时变化率）：求和
            mul.multiply_by_shifted(inf.mul)    # 乘区：并进 (1 + 提交倍率)
            inf.aspects.apply_to(a)             # 方面：只覆盖声明里给了的字段

        net *= mul                          # 实际修改 = 基础值之和 x 乘区倍率
        self._bio.current_state.add_scaled(net, tick.dt_hours)
        self._clamp_state()

        a.clock_hour = (a.clock_hour + dt_hours) % 24.0
        a.elapsed_hours = a.elapsed_hours + dt_hours
        self._reap(tick)

    def _apply(self, inf) -> None:
        """落地一个效果的瞬时声明（挂载那一刻，dt=0）。"""
        inf.aspects.apply_to(self._bio.aspects)
        self._bio.current_state += inf.instant
        inf.instant.zero()
        self._clamp_state()

    def _reap(self, tick: Tick) -> None:
        """到期就摘。终态由效果自己在最后一帧的声明里给出，引擎不替它写状态。"""
        self._effects = [eff for eff in self._effects if eff.alive(self._bio, tick)]

    def _clamp_state(self) -> None:
        s = self._bio.current_state
        for name, dim in self._dimensions.items():
            setattr(s, name, dim.clamp(getattr(s, name)))
