import enum
import json
import time
import threading
from dataclasses import dataclass
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]


# ==================== 配置类 ====================
@dataclass
class BiorythmConfig:
    """生物节律状态机全部可调参数"""

    # ---------- 睡眠 ----------
    sleep_full_duration: float = 8.0            # 睡足所需小时数
    doze_timeout: float = 2.0                   # 赖床后自动清醒等待时间（h）

    # ---------- 能量 ----------
    energy_awake_cost: float = 2.0              # 清醒每小时消耗
    energy_sleep_recover: float = 5.0           # 睡眠每小时恢复
    energy_doze_recover: float = 1.0            # 赖床每小时恢复
    energy_max: float = 100.0
    energy_min: float = 0.0
    energy_eat_gain: float = 10.0               # 进食恢复能量
    energy_exercise_cost_base: float = 15.0     # 运动基础消耗（倍乘强度）
    energy_low_threshold: float = 30.0          # 低于此值倾向休息

    # ---------- 饥饿 ----------
    hunger_full_duration: float = 1.0           # FULL 持续小时数
    hunger_content_duration: float = 4.0        # CONTENT 持续小时数

    # ---------- 活动 ----------
    exercise_cooldown: float = 1.0              # 运动后保持 ACTIVE 的时长

    # ---------- 压力 ----------
    stress_rise_rate: float = 10.0              # 基础压力上升速率
    stress_fall_rate: float = 5.0               # 基础压力下降速率
    stress_irritable_threshold: float = 70.0    # 压力超过此值为 irritable
    stress_happy_threshold: float = 30.0        # 压力低于此值且能量高时为 happy
    stress_happy_energy_required: float = 60.0
    stress_energy_low_threshold: float = 40.0   # 能量低于此值开始升压
    stress_hunger_factor: float = 0.8           # 饥饿状态附加压力系数
    stress_doze_factor: float = 0.3             # 赖床附加压力系数
    stress_mood_recovery_neutral: float = 50.0
    stress_mood_recovery_happy: float = 40.0

    # ---------- 模拟精度 ----------
    time_step: float = 0.05                     # 内部更新步长（h）

    def save(self, filepath: str):
        with open(filepath, 'w') as f:
            json.dump(self.__dict__, f, indent=4)

    @classmethod
    def load(cls, filepath: str) -> 'BiorythmConfig':
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls(**data)


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


# ==================== 状态机主体 ====================
class CircadianStateMachine:
    """使用可配置参数的多维度生物节律状态机"""

    def __init__(self, config: Optional[BiorythmConfig] = None, start_hour: float = 8.0):
        self.cfg = config if config else BiorythmConfig()
        self.time = start_hour % 24.00

        # 状态变量
        self.sleep_state = SleepState.AWAKE
        self.sleep_duration = 0.0
        self.doze_timer = 0.0

        self.activity = ActivityLevel.MODERATE
        self.exercise_timer = 0.0

        self.hunger = HungerState.CONTENT
        self.hunger_timer = 0.0

        self.mood = MoodState.NEUTRAL
        self.stress = 0.0

        self.energy = self.cfg.energy_max * 0.8
        self.total_elapsed = 0.0

    # ---------- 时间推进 ----------
    def advance_time(self, hours: float):
        step = self.cfg.time_step
        remaining = hours
        while remaining > 0:
            delta = min(step, remaining)
            self._update(delta)
            remaining -= delta

    def _update(self, dt: float):
        self.time = (self.time + dt) % 24.0
        self.total_elapsed += dt

        self._update_energy(dt)
        self._update_sleep(dt)
        self._update_hunger(dt)
        self._update_activity(dt)
        self._update_stress(dt)
        self._update_mood_from_stress()

    # ---------- 能量 ----------
    def _update_energy(self, dt: float):
        if self.sleep_state == SleepState.AWAKE:
            self.energy = max(self.cfg.energy_min,
                              self.energy - self.cfg.energy_awake_cost * dt)
        elif self.sleep_state == SleepState.DOZING:
            self.energy = min(self.cfg.energy_max,
                              self.energy + self.cfg.energy_doze_recover * dt)
        else:
            self.energy = min(self.cfg.energy_max,
                              self.energy + self.cfg.energy_sleep_recover * dt)

    # ---------- 睡眠（含赖床过渡）----------
    def _update_sleep(self, dt: float):
        if self.sleep_state in (SleepState.LIGHT_SLEEP, SleepState.DEEP_SLEEP, SleepState.REM):
            self.sleep_duration += dt
            if self.sleep_duration >= self.cfg.sleep_full_duration:
                self._enter_dozing()
            else:
                cycle_pos = (self.sleep_duration % 1.5) / 1.5
                if cycle_pos < 0.2:
                    self.sleep_state = SleepState.LIGHT_SLEEP
                elif cycle_pos < 0.6:
                    self.sleep_state = SleepState.DEEP_SLEEP
                else:
                    self.sleep_state = SleepState.REM
        elif self.sleep_state == SleepState.DOZING:
            self.doze_timer += dt
            self.sleep_duration += dt
            if self.doze_timer >= self.cfg.doze_timeout:
                self._exit_dozing_to_awake()

    def _enter_dozing(self):
        self.sleep_state = SleepState.DOZING
        self.doze_timer = 0.0

    def _exit_dozing_to_awake(self):
        self.sleep_state = SleepState.AWAKE
        self.sleep_duration = 0.0
        self.doze_timer = 0.0

    # ---------- 饥饿（自动变饿）----------
    def _update_hunger(self, dt: float):
        self.hunger_timer += dt
        if self.hunger == HungerState.FULL and self.hunger_timer >= self.cfg.hunger_full_duration:
            self.hunger = HungerState.CONTENT
            self.hunger_timer = 0.0
        elif self.hunger == HungerState.CONTENT and self.hunger_timer >= self.cfg.hunger_content_duration:
            self.hunger = HungerState.HUNGRY
            self.hunger_timer = 0.0

    # ---------- 活动（含运动惯性）----------
    def _update_activity(self, dt: float):
        if self.sleep_state != SleepState.AWAKE:
            self.activity = ActivityLevel.REST
            self.exercise_timer = 0.0
            return

        if self.exercise_timer > 0:
            self.exercise_timer -= dt
            if self.exercise_timer <= 0:
                self.activity = ActivityLevel.MODERATE
        else:
            if self.energy < self.cfg.energy_low_threshold:
                self.activity = ActivityLevel.REST
            else:
                self.activity = ActivityLevel.MODERATE

    # ---------- 压力系统 ----------
    def _update_stress(self, dt: float):
        stress_gain = 0.0
        if self.energy < self.cfg.stress_energy_low_threshold:
            energy_deficit_ratio = (self.cfg.stress_energy_low_threshold - self.energy) / self.cfg.stress_energy_low_threshold
            stress_gain += self.cfg.stress_rise_rate * energy_deficit_ratio

        if self.hunger == HungerState.HUNGRY:
            stress_gain += self.cfg.stress_rise_rate * self.cfg.stress_hunger_factor

        if self.sleep_state == SleepState.DOZING:
            stress_gain += self.cfg.stress_rise_rate * self.cfg.stress_doze_factor

        stress_loss = 0.0
        if self.energy > self.cfg.stress_energy_low_threshold and self.hunger != HungerState.HUNGRY:
            stress_loss = self.cfg.stress_fall_rate
        elif self.energy > self.cfg.stress_energy_low_threshold * 0.5 and self.hunger == HungerState.CONTENT:
            stress_loss = self.cfg.stress_fall_rate * 0.5

        self.stress = max(0.0, min(100.0, self.stress + (stress_gain - stress_loss) * dt))

    # ---------- 情绪（带滞后）----------
    def _update_mood_from_stress(self):
        if self.mood == MoodState.IRRITABLE:
            if self.stress < self.cfg.stress_mood_recovery_neutral:
                self.mood = MoodState.NEUTRAL
        elif self.mood == MoodState.HAPPY:
            if self.stress > self.cfg.stress_mood_recovery_happy:
                self.mood = MoodState.NEUTRAL
        else:  # NEUTRAL
            if self.stress >= self.cfg.stress_irritable_threshold:
                self.mood = MoodState.IRRITABLE
            elif self.stress <= self.cfg.stress_happy_threshold and self.energy > self.cfg.stress_happy_energy_required:
                self.mood = MoodState.HAPPY

    # ---------- 查询接口 ----------
    def get_state(self) -> Dict[str, Any]:
        return {
            "time": round(self.time, 1),
            "sleep": self.sleep_state.value,
            "activity": self.activity.value,
            "hunger": self.hunger.value,
            "mood": self.mood.value,
            "energy": round(self.energy, 1),
            "stress": round(self.stress, 1),
            "sleep_duration": round(self.sleep_duration, 1),
            "hours_since_eat": round(self.hunger_timer, 1),
        }

    # ---------- 主动接口 ----------
    def force_sleep(self):
        if self.sleep_state in (SleepState.AWAKE, SleepState.DOZING):
            self.sleep_state = SleepState.LIGHT_SLEEP
            self.sleep_duration = 0.0
            self.doze_timer = 0.0
            self.exercise_timer = 0.0

    def force_wake(self):
        if self.sleep_state != SleepState.AWAKE:
            self.sleep_state = SleepState.AWAKE
            self.sleep_duration = 0.0
            self.doze_timer = 0.0

    def eat(self):
        self.hunger = HungerState.FULL
        self.hunger_timer = 0.0
        self.energy = min(self.cfg.energy_max, self.energy + self.cfg.energy_eat_gain)

    def exercise(self, intensity: float = 1.0):
        if self.sleep_state != SleepState.AWAKE:
            raise RuntimeError("不能在卧床时运动")
        self.activity = ActivityLevel.ACTIVE
        self.exercise_timer = self.cfg.exercise_cooldown
        self.energy = max(self.cfg.energy_min,
                          self.energy - self.cfg.energy_exercise_cost_base * intensity)


# ==================== 多线程模拟引擎 ====================
class BioSim:
    """
    多线程生物节律模拟引擎。
    后台线程自动推进模拟时间，支持动态调整时间倍率。
    """

    def __init__(self, config: Optional[BiorythmConfig] = None, start_hour: float = 8.0,
                 update_interval: float = 0.1, time_scale: float = 0.1):
        """
        :param config: 配置对象，None 则使用默认
        :param start_hour: 初始时刻（24小时制）
        :param update_interval: 后台线程更新间隔（现实秒）
        :param time_scale: 模拟时间倍率（模拟小时 / 现实秒）
        """
        self.cfg = config if config else BiorythmConfig()
        self.machine = CircadianStateMachine(self.cfg, start_hour)
        self.update_interval = update_interval
        self.time_scale = time_scale

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        """启动后台模拟线程（非阻塞）"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """停止后台模拟线程，阻塞等待线程结束"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _run(self):
        while not self._stop_event.is_set():
            time.sleep(self.update_interval)
            if self._stop_event.is_set():
                break
            # 推进模拟时间 = 现实间隔 × 倍率
            elapsed = self.update_interval * self.time_scale
            with self._lock:
                self.machine.advance_time(elapsed)

    def set_time_scale(self, scale: float):
        """动态调整时间倍率（模拟小时/现实秒）"""
        with self._lock:
            self.time_scale = max(0.0, scale)

    # ---------- 线程安全的主动接口 ----------
    def eat(self):
        with self._lock:
            self.machine.eat()

    def exercise(self, intensity: float = 1.0):
        with self._lock:
            self.machine.exercise(intensity)

    def force_sleep(self):
        with self._lock:
            self.machine.force_sleep()

    def force_wake(self):
        with self._lock:
            self.machine.force_wake()

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return self.machine.get_state()


# ==================== 多线程使用演示 ====================
if __name__ == "__main__":
    # 使用快速变化的配置（方便观察效果）
    fast_config = BiorythmConfig(
        sleep_full_duration=3.0,
        doze_timeout=0.2,
        energy_awake_cost=8.0,
        energy_sleep_recover=15.0,
        hunger_full_duration=0.5,
        hunger_content_duration=1.0,
        exercise_cooldown=0.4,
        stress_rise_rate=30.0,
        stress_fall_rate=8.0,
        stress_irritable_threshold=60.0,
        stress_happy_threshold=25.0,
        stress_happy_energy_required=50.0,
        stress_energy_low_threshold=50.0,
        stress_mood_recovery_neutral=40.0,
        stress_mood_recovery_happy=30.0,
        time_step=0.05
    )

    # 创建引擎，time_scale=0.1 表示每秒推进 0.1 小时（6分钟）
    engine = BioSim(config=fast_config, start_hour=8.0,
                              update_interval=0.5, time_scale=0.1)

    print("=== 多线程生物节律模拟启动 ===")
    print("提示：引擎每秒自动推进 0.1 模拟小时（6分钟）")
    print("主线程每 2 秒打印一次状态，同时你可以在主线程调用主动接口")
    engine.start()

    try:
        cycle = 0
        while True:
            cycle += 1
            time.sleep(2.0)  # 主线程每 2 秒查询一次

            # 演示动态调整倍率（循环中加速再减速）
            if cycle == 5:
                print("\n>>> 加速：设置 time_scale = 0.5 (每秒推进 0.5 小时)")
                engine.set_time_scale(0.5)
            elif cycle == 10:
                print("\n>>> 减速：设置 time_scale = 0.05 (每秒推进 0.05 小时)")
                engine.set_time_scale(0.05)
            elif cycle == 15:
                print("\n>>> 恢复默认：time_scale = 0.1")
                engine.set_time_scale(0.1)

            # 演示主动干预
            if cycle == 3:
                print("\n>>> 主动吃一顿")
                engine.eat()
            elif cycle == 7:
                print("\n>>> 主动运动 (强度1.0)")
                engine.exercise(1.0)
            elif cycle == 12:
                print("\n>>> 强制午睡")
                engine.force_sleep()
            elif cycle == 14:
                print("\n>>> 强制起床")
                engine.force_wake()

            state = engine.get_state()
            print(f"时间 {state['time']:.1f}h | 睡眠:{state['sleep']:12s} | "
                  f"活动:{state['activity']:8s} | 饥饿:{state['hunger']:7s} | "
                  f"情绪:{state['mood']:9s} | 能量:{state['energy']:5.1f} | "
                  f"压力:{state['stress']:5.1f} | 已睡:{state['sleep_duration']:4.1f}h")

    except KeyboardInterrupt:
        print("\n用户中断，正在停止引擎...")
    finally:
        engine.stop()
        print("引擎已停止。")