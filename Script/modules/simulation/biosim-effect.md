# biosim 效果插件说明书

一句话：**引擎是计算器兼时间所有者，效果只声明；参数在效果自己身上。**

## 0. 结构

```plaintext
modules/simulation/biosim/
  __init__.py          包出口：所有公开名字
  __main__.py          可运行示例
  BioEngine.py         计算器 + 时间：挂效果、按真实时间推进、交读数、存档
  Effect.py            效果契约（Effect 基类）
  EngineSlice.py       读模型：程序侧的类型化快照
  BioEnum.py           状态枚举
  BasicEffects/        效果插件
    physiology.py        常态生理 + 连续量到枚举的映射 + baseline_effects()
    physics.py           躯体行为：吃 / 运动 / 睡 / 醒（+ sleep_effect 种子注入）
  types/               值类型
    BioState.py          状态容器 + 维度声明 + 方面表
    Influence.py         效果的声明
    Tick.py              时间上下文
    Vector.py            向量与维度
```

没有独立的时钟类：`integration_step_hours`（精度）和 `sim_hours_per_real_hour`（缩放）
就是引擎的两个属性，时钟行为由它们唯一确定。

## 1. 三个角色

| 角色 | 职责 |
| --- | --- |
| `BioEngine` | 计算器 + 时间所有者：挂效果、按真实时间推进、交读数、存档。**不认识业务、不认识动作、不认识措辞** |
| `Effect` | 参数在构造时给；每次被问到时只拿到 `(state, tick)`，声明这一帧的贡献。全程声明式 |
| `EngineSlice` | 类型化读模型，也是黑箱化的边界 |

## 2. 构造与挂载

```python
engine = BioEngine(start_clock_hour=8.0,        # 只在没有存档时用得上
                   integration_step_hours=0.05, # 积分精度
                   sim_hours_per_real_hour=1.0, # 模拟缩放
                   checkpoint="working_cache/biosim")   # 有存档就恢复

if not engine.loaded_from_checkpoint:           # 判据是"读档成功了吗"，不是"有没有效果"
    engine.add_effects(*baseline_effects())     # 常态：一组普通效果

engine.add_effect(EatEffect(portion=0.7, quality=0.5))   # 挂上就落地（dt=0）
engine.add_effects(SleepEffect(), CoffeeEffect())        # 一次挂多个
engine.remove_effect(effect)                    # 摘掉；不在列表里返回 False

engine.effects                                  # 只读快照
engine.advance(2.0)                             # 手动推进（按精度分步 + 零头结算）
engine.sync()                                   # 按真实时间同步到此刻
engine.get_slice()                              # 读数（会先同步到此刻）
```

没有动作名、没有注册表、没有参数类型表 —— **要做什么就构造一个效果对象挂上去**。
"动作"就是"一个会自己过期的效果"。

## 3. 参数在哪：没有 config 这个东西

| 什么参数 | 归谁 |
| --- | --- |
| 效果自己的数字（消化多久、恢复多快、乘区多少…） | **效果的构造参数** |
| 维度的范围 / 初值 / 读数档位 | **`types/BioState.py` 里的 `Dimension` 声明** |
| 积分精度 | `BioEngine(integration_step_hours=...)` |
| 模拟缩放 | `BioEngine(sim_hours_per_real_hour=...)` |
| 随机源 | 注入到要随机的那个效果（`physics.sleep_effect(seed=...)`） |
| 一整套平衡 | **一个返回效果列表的普通函数**（如 `physiology.baseline_effects()`），数值写在函数体里 |

理由是同一个：**参数紧挨着它配置的东西**。一袋全局配置会把效果的真实依赖藏起来
（看签名看不出 `EnergyDynamics` 需要什么），也让"两套不同参数的模拟"变得别扭。

```python
engine.add_effect(EnergyDynamics(base_cost_per_hour=6.0))        # 这个世界的代谢更费
engine.add_effect(FullnessDynamics(decay_per_hour=5.0))          # 而且饿得慢
```

## 4. 效果契约（Effect.py）

| 方法 | 调用时机 | 契约 |
| --- | --- | --- |
| `influence(state, tick) -> Influence` | 每帧 + 落地那一次 | **唯一必须实现的**。只声明，不改状态 |
| `alive(state, tick) -> bool` | 每帧末 | `False` 则本帧被摘掉。**终态在它最后一帧的 `influence` 里声明**，没有收尾函数 |
| `refusal(slice) -> str \| None` | `add_effect` 之前 | 挂上去有没有意义；没意义就把原因说出来 |

注意签名里**没有 `cfg`**：效果的外部依赖只有它自己的构造参数。
`refusal` 收的是 `EngineSlice`（外面看得见的读数），所以任何消费方都能问。

**没有任何直接写 `state` 的入口**：连到期收尾都得变成声明。老版本有个 `on_expire`
例外，已经删了 —— 一个能改状态的例外会让"只声明"这条线失去意义。

## 5. 四个通道

| 通道 | 语义 | 引擎怎么整合 |
| --- | --- | --- |
| `delta_per_hour` | **基础值**：每模拟小时的变化率 | 各效果**相加** |
| `mul` | **乘区**：提交的是**倍率增量**，单位元 **0.0**（0 = 无影响，-0.3 = 降低 30%） | 每个效果算 `(1 + 提交值)`，各效果**相乘** |
| `instant` | **瞬时跳变** | 只在效果落地那一刻应用一次，随后清零，不吃乘区 |
| `aspects` | **方面声明**：`AspectPatch`（字段可空，静态可查） | 每帧合并进状态的 `Aspects`（齐全，少一个字段就报错） |

```plaintext
实际修改量 = ( 基础值之和 ) x Π( 1 + 各效果提交的倍率 ) x dt
```

**基础值**用来"添一笔"（睡眠在回电就交 +9/h），**乘区**用来"按比例改基准"
（睡眠让血糖消耗降 60% 就交 -0.6）。两个各交 -0.3 的效果得到 `0.7 x 0.7 = 0.49`
—— **每个效果是一个独立乘区**，效果不需要知道还有谁在改同一个维度。

方面分两张表同理：状态里那份必须齐全（`Aspects`，字段无默认值，少给就报错），
效果提交的是部分声明（`AspectPatch`，字段可空）。**不要用 `.get()` 之类平息报错** ——
报错意味着类型没说清，改类型，别绕过检查。

## 6. 引擎时序（BioEngine.py）

**时间**：引擎持有 `integration_step_hours` / `sim_hours_per_real_hour` 和一个
"上次同步的真实时刻"。读取、改状态、存档前都会 `sync()`：把 `(now - 上次同步) x 缩放`
按精度分步推进。**时间由挂钟算出，不自己累计。**

**`add_effect(effect)`：**

```plaintext
1. 同步到此刻
2. 问 effect.refusal(读模型)：有原因就不挂，返回 False
3. tick 归零（dt=0）：落地不消耗时间
4. 问一次 influence()：合并 aspects、加 instant、钳制
5. 进效果列表
6. 回收一遍（一次性效果在这里就到期了，例如 WakeEffect）
```

**`advance(hours)` → 内部按 `integration_step_hours` 分步 `_step(dt)`：**

```plaintext
1. tick.set(dt, clock_hour, elapsed_hours)
2. net 清零；mul 填单位元 1.0
3. 逐个 effect.influence()：net += delta_per_hour；mul 并进 (1 + 提交倍率)；aspects 合并
4. net = 基础值之和 x 乘区倍率，再 current_state += net * dt
5. 按各维度的 [low, high] 钳制
6. clock_hour / elapsed_hours 前进
7. 逐个 alive()：False 的从列表移除（它最后一帧已经声明过终态）
```

## 7. 一个完整的效果

```python
class CoffeeEffect(Effect):
    """喝咖啡：当场入血（瞬时），之后一段时间提神降压力。"""

    def __init__(self, *, shots: float = 1.0, minutes: float = 30.0,
                 energy_rate_per_hour: float = 12.0, stress_rate_per_hour: float = 20.0) -> None:
        self.energy_rate_per_hour = energy_rate_per_hour   # 参数全在构造时给
        self.stress_rate_per_hour = stress_rate_per_hour
        self.total_hours = minutes / 60.0
        self._inf = Influence()                           # 热路径复用，不在帧里 new
        self._inf.instant.glucose = 6.0 * shots           # 落地那一刻入血

    def refusal(self, slice: EngineSlice) -> str | None:
        if slice.sleep is SleepState.AWAKE:
            return None
        return "你已经睡了，喝了也提不了神。"

    def influence(self, state: BioState, tick: Tick) -> Influence:
        inf = self._inf
        self._inf.delta_per_hour.energy = self.energy_rate_per_hour
        inf.aspects.stress = max(0.0, state.aspects.stress - self.stress_rate_per_hour * tick.dt_hours)
        return inf

    def alive(self, state: BioState, tick: Tick) -> bool:
        return tick.dt_hours <= 0.0 or state.aspects.stress > 0.0   # 示意；真实效果自己记进度
```

挂上：`engine.add_effect(CoffeeEffect(shots=2))`（放进 `BasicEffects/`，或你自己的模块）

> 用 `self._inf` 复用缓冲时，**每一帧要把自己用到的字段完整写一遍**：引擎只保证
> "每帧恰好问一次 `influence`"，上一帧残留的 `delta_per_hour` / `mul` 不会自动清。
> 需要分支声明的效果（如 `SleepEffect`）在函数开头 `inf.delta_per_hour.zero()`。

## 8. 改东西 = 改哪里

| 想改什么 | 改哪里 |
| --- | --- |
| 加/删一个连续维度 | `types/BioState.py` 的 `StateVec.elements`（范围、初值、档位一起写）+ 那个类的 `__init__` 加一行 |
| 加一个离散/标量方面 | `types/BioState.py`：`Aspects` 与 `AspectPatch` 各加一行（对不上会在 import 时抛错） |
| 改某个效果的数值 | 那个效果的构造参数，或 `baseline_effects()` 里传的值 |
| 换一套整体平衡 | 自己写一个返回效果列表的函数，装配处 `add_effects(*...)` |
| 加一个可挂载的行为 | 在 `BasicEffects/physics.py`（或你自己的模块）写一个 `Effect` 子类，装配处 `add_effect()` |
| 加一个常态生理 | 在 `BasicEffects/physiology.py` 写一个 `Effect` 子类，加进 `baseline_effects()` |
| 让**程序**读到新量 | `EngineSlice.py` 加字段 + `BioEngine._slice_locked()` 填上 |
| 让**模型**看到 | `modules/tools/impl/bio_text.py` 加占位符（文本是工具，不进核心） |

维度的档位写在维度上：`Dimension("fullness", initial=100.0, bands=(25.0, 60.0))`
就是"低于 25 算饿、到 60 算饱"。读数分档由 `band_of()` 查出来，不是 if 链；
`hunger_of / mood_of / glycemia_of` 就在 `BasicEffects/physiology.py` 里查这张表。

## 9. 红线

1. `influence` 里**不许改状态** —— 声明，不执行；**没有例外**。
2. **不许在热路径分配**：`self._inf` 在 `__init__` 建一次、复用；复用就要每帧把自己用到的字段写全。
3. **不许阻塞**：它在锁内每帧跑。
4. `instant` 只在落地那一刻有效，引擎用完即清零。
5. 效果之间**不要互相引用**，靠状态通信。
6. 效果要的外部依赖只有构造参数；**别去够全局配置**（没有这个东西了）。
7. 乘区交的是**增减**（单位元 0.0），不要交倍率本身，也别拿它表达"添一笔"。
8. **不要在效果里自己算最终值** —— 整合是引擎的事。
9. 效果对象**有可变状态**（进度、随机源），一个对象只挂一次；要再来一次就新建一个。
10. 方面按类型分两头：状态是 `Aspects`（齐全，直接读），声明是 `AspectPatch`（部分，直接写字段）。
    **不许用 `.get()` 平息报错** —— 报错意味着类型没说清，改类型，别绕过检查。
11. 单位后缀别省：时长 `*_hours`、速率 `*_per_hour`、真实秒 `*_seconds`。

## 10. 内置效果与默认参数

| 住在哪 | 名字 | 构造参数（都有默认值） |
| --- | --- | --- |
| physiology | `EnergyDynamics` | `base_cost_per_hour=2.0, low_glycemia_cost_per_hour=4.0` |
| physiology | `GlucoseDynamics` | `decay_per_hour=6.0` |
| physiology | `FullnessDynamics` | `decay_per_hour=20.0` |
| physiology | `StressDynamics` | `rise_rate_per_hour=10.0, fall_rate_per_hour=5.0, energy_low=40.0, hunger_factor=0.8` |
| physiology | `MoodDynamics` | `response_rate_per_hour=2.0, happy_stress=30.0, happy_energy=60.0, irritable_stress=70.0` |
| physics | `EatEffect` | `portion=0.5, quality=1.0, digest_hours=1.0, fullness_gain=60.0, glucose_gain=70.0, energy_gain=8.0, mood_gain=12.0` |
| physics | `ExerciseEffect` | `intensity=1.0, minutes=20.0, energy_rate_per_hour=4.0, glucose_rate_per_hour=12.0` |
| physics | `SleepEffect` | `recovery_per_hour=9.0, wake_rate_per_hour=1.5, wake_sharpness=30.0, cycle_hours=1.5, glucose_factor=-0.6, rng=...` |
| physics | `WakeEffect` | — |

前五个由 `physiology.baseline_effects()` 按上表的数字返回。
睡眠时长没有配置项：它是"回满精力要多久"的结果（净 +7/h），再叠上按概率掷出来的随机。

## 11. 存档（checkpoint）

```python
engine = BioEngine(checkpoint="/path/to/ckpt")   # 构造时给路径；有存档就恢复
engine.save_checkpoint()                         # 内部先同步到此刻，再切片落盘
engine.loaded_from_checkpoint                    # 这次是否真的读到了存档
```

- 存的是**状态 + 当时挂着的效果 + 存档时刻 + 缩放**。效果也是状态：消化到一半、
  正在运动、睡着 —— 只存数字不够。
- **给了路径 ≠ 那里就有存档**：没有就当新的一天；判据是 `loaded_from_checkpoint`，
  不要用"有没有效果"来判断（空世界的存档会被误判）。
- 同日重启：按存档时刻与缩放把停机段补算进状态；**跨天则不补、当新的一天**。
  跨天不补是有意的：中间的外界事件（吃饭、出门）不该被"演"出来。
- 写盘**先同步、再在锁内取切片、最后锁外落盘**（IO 不占状态锁）；落盘是原子的
  （先写 `.tmp` 再 `os.replace`）。
- **文件在、却读不了会抛**（`UnpicklingError` / `ValueError`）—— 那是异常，不是"还没有存档"。
- 格式是内部自定的 pickle：**改了效果字段名之后旧存档读不回来**。这是有意的 ——
  读不了就报错，不猜。调用方该 `try/except` 兜住并退回新的一天。

注意一个容易踩的点：`add_effect` 落地那一刻也会调 `influence()`，而那次 `tick.dt_hours == 0`。
效果若要声明"进度"这类状态，得先判断 `tick.dt_hours > 0` 再递减，否则会把自己刚声明的进度减掉、当场被判死。
