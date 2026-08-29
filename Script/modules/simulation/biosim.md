# 生物节律状态机 – 多线程模拟引擎

## 概述

本模块实现了一个多维度的生物节律状态机，模拟人体在睡眠‑觉醒、能量、饥饿、活动、压力与情绪之间的动态耦合。状态机支持主动干预（进食、运动、强制睡眠/唤醒），并提供了一个**多线程模拟引擎**，可在后台持续推进模拟时间，同时支持实时调整时间倍率，无需外部循环驱动。

## 依赖

- Python 3.7+（因使用 `dataclasses` 和 `enum`）
- 标准库：`threading`, `time`, `json`, `enum`, `dataclasses`, `typing`

无需安装第三方包。

## 核心类与配置

### `BiorythmConfig`

所有可调参数的容器。使用 `dataclass` 定义，提供 `save()` 和 `load()` 方法用于持久化。

**主要参数分组**（完整列表见源码）：

| 分组 | 参数 | 说明 |
| ------ | ------ | ------ |
| 睡眠 | `sleep_full_duration` | 睡足所需小时数 |
| | `doze_timeout` | 赖床后自动清醒的等待时间（h） |
| 能量 | `energy_awake_cost` | 清醒时每小时消耗 |
| | `energy_sleep_recover` | 睡眠时每小时恢复 |
| | `energy_doze_recover` | 赖床时每小时恢复 |
| | `energy_max` / `energy_min` | 能量上下限 |
| | `energy_eat_gain` | 每次进食恢复量 |
| | `energy_exercise_cost_base` | 运动基础消耗（乘以强度） |
| | `energy_low_threshold` | 低于此值倾向休息 |
| 饥饿 | `hunger_full_duration` | `FULL` 状态持续小时数 |
| | `hunger_content_duration` | `CONTENT` 状态持续小时数 |
| 活动 | `exercise_cooldown` | 运动后保持 `ACTIVE` 的时长（h） |
| 压力 | `stress_rise_rate` / `stress_fall_rate` | 基础升降速率 |
| | `stress_irritable_threshold` | 压力超过此值为 `irritable` |
| | `stress_happy_threshold` | 压力低于此值且能量足够时为 `happy` |
| | `stress_happy_energy_required` | 进入 `happy` 所需最低能量 |
| | `stress_energy_low_threshold` | 能量低于此值开始升压 |
| | `stress_hunger_factor` / `stress_doze_factor` | 饥饿/赖床附加压力系数 |
| | `stress_mood_recovery_neutral` / `happy` | 情绪迁出阈值 |
| 模拟 | `time_step` | 内部积分步长（h），影响计算精度 |

### 状态枚举

- `SleepState`：`AWAKE` / `DOZING` / `LIGHT_SLEEP` / `DEEP_SLEEP` / `REM`
- `ActivityLevel`：`REST` / `MODERATE` / `ACTIVE`
- `HungerState`：`FULL` / `CONTENT` / `HUNGRY`
- `MoodState`：`NEUTRAL` / `HAPPY` / `IRRITABLE`

## 状态机 `CircadianStateMachine`

### 初始化

```python
machine = CircadianStateMachine(config=None, start_hour=8.0)
```

- `config`：`BiorythmConfig` 实例，若为 `None` 则使用默认参数。
- `start_hour`：初始模拟时间（24 小时制）。

### 内部更新

状态机通过 `advance_time(hours)` 推进模拟时间，内部按 `time_step` 步长积分所有状态变量。**在多线程引擎中，此方法由后台线程自动调用**，用户无需手动调用。

### 主动接口

所有方法均为线程安全（需配合 `SimulationEngine` 的锁机制）：

- `force_sleep()`：若当前为 `AWAKE` 或 `DOZING`，立即进入 `LIGHT_SLEEP`，重置睡眠计时。
- `force_wake()`：强制切换至 `AWAKE`，重置所有睡眠相关计时器。
- `eat()`：将饥饿设为 `FULL`，重置进食计时器，并恢复 `energy_eat_gain` 点能量（不超过上限）。
- `exercise(intensity=1.0)`：仅在 `AWAKE` 时有效，将活动设为 `ACTIVE`，重置运动冷却，并扣除 `energy_exercise_cost_base * intensity` 能量。若在卧床状态调用将抛出 `RuntimeError`。

### 查询状态

```python
state = machine.get_state()
```

返回字典包含：`time`, `sleep`, `activity`, `hunger`, `mood`, `energy`, `stress`, `sleep_duration`, `hours_since_eat`。

## 多线程模拟引擎 `SimulationEngine`

`SimulationEngine` 封装了状态机和一个后台线程，自动以指定速率推进模拟时间，并提供线程安全的控制接口。

### 初始化

```python
engine = SimulationEngine(config=None, start_hour=8.0, update_interval=0.1, time_scale=0.1)
```

- `config` / `start_hour`：同 `CircadianStateMachine`。
- `update_interval`：后台线程的睡眠间隔（秒），决定状态采样的频率。
- `time_scale`：模拟时间倍率，单位为“模拟小时 / 现实秒”。  
  例如 `time_scale=0.1` 表示每现实秒推进 0.1 小时（即 6 分钟）。默认值 0.1 与原演示程序的速度一致。

### 控制方法

- `start()`：启动后台线程（非阻塞），开始自动推进模拟。
- `stop()`：停止后台线程，阻塞等待线程结束（超时 1 秒）。
- `set_time_scale(scale: float)`：动态调整倍率，即时生效。

### 主动接口（线程安全）

与状态机接口同名，自动加锁，可在任意线程调用：

- `eat()`, `exercise(intensity)`, `force_sleep()`, `force_wake()`

### 查询状态

- `get_state()`：返回当前状态字典，线程安全。

## 使用示例（不含调试循环）

```python
from biorhythm import SimulationEngine, BiorythmConfig

# 自定义配置
config = BiorythmConfig(
    sleep_full_duration=6.0,
    energy_awake_cost=3.0,
    stress_rise_rate=15.0
)

# 创建引擎，默认 time_scale=0.1
engine = SimulationEngine(config, start_hour=6.0)
engine.start()

# 主线程可做其他事情，例如每隔 2 秒打印一次状态
import time
try:
    while True:
        state = engine.get_state()
        print(f"{state['time']}h  energy={state['energy']}")
        time.sleep(2)
except KeyboardInterrupt:
    engine.stop()

# 在任意时刻调用主动行为
engine.eat()
engine.exercise(intensity=1.5)
engine.force_sleep()
# 调整倍率加速
engine.set_time_scale(0.5)   # 现在每秒推进 0.5 小时
```

## 配置持久化

```python
config.save("my_config.json")
loaded = BiorythmConfig.load("my_config.json")
```

## 注意事项

- 所有时间单位为“小时”，内部积分步长默认为 0.05 h（3 分钟），可在配置中调整。
- 多线程环境下，主动接口与后台更新均加锁保护，避免数据竞争。
- 停止引擎时请务必调用 `stop()` 以正确释放线程资源。
- 倍率调整不会影响已累积的模拟时间，仅改变后续推进速度。
