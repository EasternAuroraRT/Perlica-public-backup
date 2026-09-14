# biosim 效果插件说明书

一句话：**引擎是计算器，效果只声明；参数在效果自己身上。**

## 0. 结构

```
modules/simulation/biosim/
  __init__.py          包出口：所有公开名字
  __main__.py          可运行示例
  BioEngine.py         计算器：挂效果、推进时间、交读数
  Effect.py            效果契约（Effect 基类）
  EngineClock.py       时钟：按真实时间驱动计算器
  EngineSlice.py       读模型：程序侧的类型化快照
  BioEnum.py           状态枚举
  templates.py         现成的装配模板（= 调参面）
  BasicEffects/        效果插件
    physiology.py        常态生理 + 连续量到枚举的映射
    physics.py           躯体行为：吃 / 运动 / 睡 / 醒
  types/               值类型
    BioState.py          状态容器 + 维度声明 + 方面表
    Influence.py         效果的声明
    Tick.py              时间上下文
    Vector.py            向量与维度
```

命名规矩：`Bio*` 是模拟的主体（`BioEngine` / `BioState` / `BioEnum`），
`Engine*` 是引擎周边的设施（`EngineClock` / `EngineSlice`），`Effect.py` 是契约，
效果插件住在 `BasicEffects/`，纯值类型住在 `types/`。

## 1. 三个角色

| 角色 | 职责 |
| --- | --- |
| `BioEngine` | 计算器：挂效果、推进时间、交读数。**不认识业务、不认识动作、不认识配置** |
| `Effect` | 参数在构造时给；每次被问到时只拿到 `(state, tick)`，声明这一帧的贡献 |
| `EngineSlice` | 类型化读模型，也是黑箱化的边界 |

时钟（谁按真实时间驱动计算器）在 `EngineClock` 里：引擎只认 `dt`。

## 2. 挂一个效果

```python
engine = standard(start_hour=8.0)          # 模板：常态已挂好，拿来就用

engine.add_effect(EatEffect(portion=0.7, quality=0.5))   # 挂上就落地
engine.add_effects(SleepEffect(), CoffeeEffect())        # 一次挂多个
engine.remove_effect(effect)               # 摘掉（收尾照走），不在列表里返回 False

engine.effects                             # 只读快照
engine.advance(2.0)                        # 推进（内部按 time_step 分步）
engine.observe()                           # 读数（EngineSlice）
```

没有动作名、没有注册表、没有参数类型表 —— **要做什么就构造一个效果对象挂上去**。
"动作"就是"一个会自己过期的效果"。

## 3. 参数在哪：没有 config 这个东西

| 什么参数 | 归谁 |
| --- | --- |
| 效果自己的数字（消化多久、恢复多快、乘区多少…） | **效果的构造参数** |
| 维度的范围 / 初值 / 读数档位 | **`types/BioState.py` 里的 `Dimension` 声明** |
| 积分步长 | `BioEngine(time_step=...)` |
| 随机源 | 注入到要随机的那个效果（`templates.sleep_effect(seed=...)`） |
| 整套平衡 / 多套预设 | **模板函数**：`templates.standard` 的函数体就是调参面 |

理由是同一个：**参数紧挨着它配置的东西**。一袋全局配置会把效果的真实依赖藏起来
（看签名看不出 `EnergyDynamics` 需要什么），也让"两套不同参数的模拟"变得别扭。

```python
engine.add_effect(EnergyDynamics(base_cost=6.0))        # 这个世界的代谢更费
engine.add_effect(FullnessDynamics(decay_per_hour=5.0)) # 而且饿得慢
```

## 4. 效果契约（Effect.py）

| 方法 | 调用时机 | 契约 |
| --- | --- | --- |
| `influence(state, tick) -> Influence` | 每帧 + 落地那一次 | **唯一必须实现的**。只声明，不改状态 |
| `alive(state, tick) -> bool` | 每帧末 | `False` 则本帧被摘掉（并走 `on_expire`） |
| `on_expire(state, tick)` | 被摘掉时（自然到期、`remove_effect`） | 收尾。**唯一允许直接写状态的地方** |
| `refusal(slice) -> str \| None` | `add_effect` 之前 | 挂上去有没有意义；没意义就把原因说出来 |

注意签名里**没有 `cfg`**：效果的外部依赖只有它自己的构造参数。
`refusal` 收的是 `EngineSlice`（外面看得见的读数），所以任何消费方都能问。

## 5. 四个通道

| 通道 | 语义 | 引擎怎么整合 |
| --- | --- | --- |
| `delta` | **基础值**：每模拟小时的变化率 | 各效果**相加** |
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

**`add_effect(effect)`：**

```plaintext
1. 问 effect.refusal(observe())：有原因就不挂，返回 False
2. tick 归零（dt=0）：落地不消耗时间
3. 问一次 influence()：合并 aspects、加 instant、钳制
4. 进效果列表
5. 回收一遍（一次性效果在这里就到期了，例如 WakeEffect）
```

**`advance(hours)` → 内部分步 `_step(dt)`：**

```plaintext
1. tick.set(dt, clock_hour, elapsed_hours)
2. net 清零；mul 填单位元 1.0
3. 逐个 effect.influence()：net += 基础值；mul 并进 (1 + 提交倍率)；aspects 合并
4. net = 基础值之和 x 乘区倍率，再 current_state += net * dt
5. 按各维度的 [low, high] 钳制
6. clock_hour / elapsed_hours 前进
7. 逐个 alive()：False 的走 on_expire，然后从列表移除
```

## 7. 一个完整的效果

```python
class CoffeeEffect(Effect):
    """喝咖啡：当场入血（瞬时），之后一段时间提神降压力。"""

    def __init__(self, *, shots: float = 1.0, minutes: float = 30.0,
                 energy_rate: float = 12.0, stress_rate: float = 20.0) -> None:
        self.energy_rate = energy_rate            # 参数全在构造时给
        self.stress_rate = stress_rate
        self._left = minutes / 60.0
        self._inf = Influence()                   # 热路径复用，不在帧里 new
        self._inf.instant.glucose = 6.0 * shots   # 落地那一刻入血

    def refusal(self, slice: EngineSlice) -> str | None:
        if slice.sleep is SleepState.AWAKE:
            return None
        return "你已经睡了，喝了也提不了神。"

    def influence(self, state: BioState, tick: Tick) -> Influence:
        self._left -= tick.dt
        self._inf.delta.energy = self.energy_rate
        self._inf.aspects.stress = max(0.0, state.aspects.stress - self.stress_rate * tick.dt)
        return self._inf

    def alive(self, state: BioState, tick: Tick) -> bool:
        return self._left > 0
```

挂上：`engine.add_effect(CoffeeEffect(shots=2))`（放进 `BasicEffects/`，或你自己的模块）

## 8. 改东西 = 改哪里

| 想改什么 | 改哪里 |
| --- | --- |
| 加/删一个连续维度 | `types/BioState.py` 的 `StateVec.elements`（范围、初值、档位一起写）+ `__init__` 加一行。**引擎、效果、向量运算都不用动** |
| 加一个离散/标量方面 | `types/BioState.py`：`Aspects` 与 `AspectPatch` 各加一行（对不上会在 import 时抛错） |
| 改某个效果的数值 | 那个效果的构造参数，或 `templates.py` 里传的值 |
| 换一套整体平衡 | 改 `templates.standard`，或照着再写一个模板函数 |
| 加一个可挂载的行为 | 在 `BasicEffects/physics.py`（或你自己的模块）写一个 `Effect` 子类，装配处 `add_effect()` |
| 加一个常态生理 | 在 `BasicEffects/physiology.py` 写一个 `Effect` 子类，加进 `BASELINE` |
| 让**程序**读到新量 | `EngineSlice.py` 加字段 + `BioEngine.observe()` 填上 |
| 让**模型**看到 | `modules/tools/impl/bio_text.py` 加占位符（文本是工具，不进核心） |

维度的档位写在维度上：`Dimension("fullness", initial=100.0, bands=(25.0, 60.0))`
就是"低于 25 算饿、到 60 算饱"。读数分档由 `band_of()` 查出来，不是 if 链；
`hunger_of / mood_of / glycemia_of` 就在 `BasicEffects/physiology.py` 里查这张表。

## 9. 红线

1. `influence` 里**不许改状态** —— 声明，不执行。唯一例外是 `on_expire`。
2. **不许在热路径分配**：`self._inf` 在 `__init__` 建一次、复用。
3. **不许阻塞**：它在锁内每帧跑。
4. `instant` 只在落地那一刻有效，引擎用完即清零。
5. `on_expire` 只能写 aspects，**改不了连续维度**。
6. 效果之间**不要互相引用**，靠状态通信。
7. 效果要的外部依赖只有构造参数；**别去够全局配置**（没有这个东西了）。
8. 乘区交的是**增减**（单位元 0.0），不要交倍率本身，也别拿它表达"添一笔"。
9. **不要在效果里自己算最终值** —— 整合是引擎的事。
10. 效果对象**有可变状态**（进度、随机源），一个对象只挂一次；要再来一次就新建一个。
11. 方面按类型分两头：状态是 `Aspects`（齐全，直接读），声明是 `AspectPatch`（部分，直接写字段）。
    **不许用 `.get()` 平息报错** —— 报错意味着类型没说清，改类型，别绕过检查。

## 10. 内置效果与默认参数

| 住在哪 | 名字 | 构造参数（都有默认值） |
| --- | --- | --- |
| physiology | `EnergyDynamics` | `base_cost=2.0, low_glycemia_cost=4.0` |
| physiology | `GlucoseDynamics` | `decay_per_hour=6.0` |
| physiology | `FullnessDynamics` | `decay_per_hour=20.0` |
| physiology | `StressDynamics` | `rise_rate=10.0, fall_rate=5.0, energy_low=40.0, hunger_factor=0.8` |
| physiology | `MoodDynamics` | `response_rate=2.0, happy_stress=30.0, happy_energy=60.0, irritable_stress=70.0` |
| physics | `EatEffect` | `portion=0.5, quality=1.0, digest_hours=1.0, fullness_gain=60.0, glucose_gain=70.0, energy_gain=8.0, mood_gain=12.0` |
| physics | `ExerciseEffect` | `intensity=1.0, minutes=20.0, energy_rate=4.0, glucose_rate=12.0` |
| physics | `SleepEffect` | `recovery_per_hour=9.0, wake_rate=1.5, wake_sharpness=30.0, cycle_hours=1.5, glucose_factor=-0.6, rng=...` |
| physics | `WakeEffect` | — |

前五个组成 `BasicEffects.physiology.BASELINE`；`templates.standard` 按上表的数字把它们挂好。
睡眠时长没有配置项：它是"回满精力要多久"的结果（净 +7/h），再叠上按概率掷出来的随机。