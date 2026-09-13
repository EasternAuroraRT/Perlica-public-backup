# biosim 效果插件说明书

一句话：**效果只声明，引擎改状态。**

| 角色 | 职责 |
| --- | --- |
| 效果 `Effect` | 声明"这一帧想让状态怎么变"，自己不碰状态 |
| 引擎 `BioSimEngine` | 唯一有权改状态的地方：汇总、门控、积分、钳制、回收 |
| 读数 `Observation` | 引擎交出的类型化结果，也是黑箱化的边界 |

消费方（程序 / 模型 / 文本渲染）只跟 `Observation` 和动作名打交道，不需要认识任何效果。

---

## 1. 一个效果 = 四件东西

```python
from dataclasses import dataclass
from .config import BioSimConfig
from .types import BioState, Influence, Tick
from .enums import ControlKind
from .core import Effect, effect


@dataclass(frozen=True)
class CoffeeParams:          # (1) 类型化命令参数：构造即静态合法，不做运行时校验
    shots: float = 1.0


@effect(CoffeeParams)        # (2) 注册：参数类型 -> 效果类
class CoffeeEffect(Effect):
    name = "coffee"                  # (3) 对外身份 + 行为声明
    control = ControlKind.VOLITIONAL
    interruptible = False
    persistent = True

    # (4) 生命周期：门控 -> 落地 -> 每帧声明 -> 回收 -> 收尾
    @classmethod
    def refusal(cls, state: BioState, cfg: BioSimConfig) -> str | None: ...
    def __init__(self, cfg: BioSimConfig, params: CoffeeParams) -> None: ...
    def _compute_influence(self, state: BioState, cfg: BioSimConfig, tick: Tick) -> Influence: ...
    def alive(self, state: BioState, cfg: BioSimConfig, tick: Tick) -> bool: ...
    def on_expire(self, state: BioState, cfg: BioSimConfig, tick: Tick) -> None: ...
```

注册表的 key 是**参数类型**，不是名字：

| 你怎么下达 | 引擎怎么找到效果 |
| --- | --- |
| `engine.act(CoffeeParams(shots=2))` | 按 `type(params)` 查注册表 |
| `engine.act_by_name("coffee", shots=2)` | 按效果类的 `name` 找到类，再构造 `CoffeeParams` |

`engine.py` 里那行 `from . import actions` 就是为了触发注册。效果**写在哪都行，但必须被 import 过一次**，否则注册表里没有它。

---

## 2. 类属性

| 属性 | 类型 | 默认 | 作用 |
| --- | --- | --- | --- |
| `name` | `str` | `"effect"` | 对外动作名。`act_by_name` / `cancel` / `interrupt` / `available_actions` 全靠它 |
| `control` | `ControlKind` | `AUTONOMIC` | `VOLITIONAL` = 外在动作，自己开的自己能 `cancel`；`AUTONOMIC` = 内在进程，触发后自行运转，`cancel` 不动它 |
| `interruptible` | `bool` | `False` | 外界（闹钟、突发事件）能否用 `interrupt` 停掉它 |
| `persistent` | `bool` | `True` | `True` 进常驻列表；`False` = 一次性，落地后立刻 `on_expire`，永不进列表 |

---

## 3. 方法

| 方法 | 调用时机 | 契约 |
| --- | --- | --- |
| `refusal(cls, state, cfg)` | 类方法。`act()` 之前一次；`available_actions()` 每次遍历时 | 允许返回 `None`；不允许返回**原因字符串**，会作为异常抛给程序、也转述给模型。**门控只有这一处** |
| `__init__(cfg, params)` | 下达动作时一次 | 预先把率、时长、阈值算好存成本地属性；要瞬时跳变就填 `self._inf.instant`。`self._inf = Influence()` 在这里建一次 |
| `_compute_influence(state, cfg, tick)` | **每帧**，锁内，热路径 | 返回那个复用的 `Influence`，只声明，不改状态 |
| `alive(state, cfg, tick)` | 每帧末尾 | 返回 `False` 则本帧被回收 |
| `on_expire(state, cfg, tick)` | 回收时（自然结束、`cancel`、`interrupt` 都会走） | 收尾。**唯一允许直接写状态的地方**，且只能 `state.set_aspect(...)` |

参数类型由各子类在自己的 `__init__` 里声明（基类的 `params` 是 `Any`）。**别在热路径反复读 `self.params`** —— 该在 `__init__` 里摊开成算好的量。

---

## 4. Influence 三个通道

| 通道 | 语义 | 何时被应用 |
| --- | --- | --- |
| `delta` | **速率**，单位是"每模拟小时" | 每帧累加进 net，帧末一次性 `current_state += net * dt` |
| `instant` | **瞬时矢量跳变** | 只在动作下达那一刻应用一次，引擎应用后立刻清零 |
| `aspects` | 离散枚举与连续标量（`sleep`、`stress`、各计时器、时钟…） | 每帧写入，同名后写覆盖先写 |

```python
def _compute_influence(self, state, cfg, tick):
    self._inf.delta.glucose = self._glucose_rate             # 每小时的量
    self._inf.aspects["activity"] = ActivityLevel.ACTIVE     # 直接写成这个值
    self.remaining -= tick.dt
    return self._inf
```

热路径零分配的写法就是：`self._inf` 建一次、原地改字段、原样返回。

---

## 5. Tick 的四个字段

| 字段 | 含义 |
| --- | --- |
| `dt` | 本帧步长，单位小时（等于 `config.time_step`） |
| `sim_now` | 当前模拟时刻 |
| `elapsed` | 引擎启动以来累计的小时数 |
| `scale` | 当前时间倍率 |

---

## 6. 引擎时序

**下达动作 `act()`：**

```plaintext
1. 查注册表：参数类型 -> 效果类
2. 门控：cls.refusal(state, cfg) 返回原因就 raise，什么都不发生
3. tick 归零（dt=0）：下达那一刻不消耗时间
4. 构造效果，并调用一次 influence()
5. 写 aspects
6. 把 instant 加到 current_state，随即清零，并钳制
7. persistent -> 进常驻列表；否则立刻 on_expire（一次性动作）
```

**每帧 `_update(dt)`：**

```plaintext
1. tick.set(dt, clock_hour, elapsed, scale)
2. net 清零
3. 逐个 effect.influence()：net += delta；aspects 就地写入（后写覆盖先写）
4. current_state += net * dt        <- 连续维度在这里一次性积分
5. 钳制到各维度的 [min, max]
6. clock_hour / elapsed_hours 前进
7. 逐个 alive()：False 的走 on_expire，然后从列表里移除
```

三点推论：

- 效果之间**没有先后依赖**，因为连续维度是最后统一积分的；只有 `aspects` 会互相覆盖，那才需要注意注册顺序。
- 本帧写入的 aspect 会立刻被同帧的其他效果读到。
- 回收发生在积分之后，所以"最后一帧"的影响照样生效。

---

## 7. 完整示例（已跑通，也过了 pyright）

```python
from dataclasses import dataclass

from .config import BioSimConfig
from .types import BioState, Influence, Tick
from .types import SleepState
from .enums import ControlKind
from .core import Effect, effect


@dataclass(frozen=True)
class CoffeeParams:
    shots: float = 1.0


@effect(CoffeeParams)
class CoffeeEffect(Effect):
    name = "coffee"
    control = ControlKind.VOLITIONAL     # 自己能开，也能自己 cancel
    interruptible = False                # 外界打断不了
    persistent = True                    # 常驻，直到 alive() 说不要了

    @classmethod
    def refusal(cls, state: BioState, cfg: BioSimConfig) -> str | None:
        if state.sleep is not SleepState.AWAKE:
            return "你已经睡了，喝了也提不了神。"
        return None

    def __init__(self, cfg: BioSimConfig, params: CoffeeParams) -> None:
        super().__init__(cfg, params)
        self._shots = params.shots               # 参数在这里摊开成算好的量
        self.remaining = 0.5 * self._shots
        self._energy_rate = 12.0 * self._shots
        self._stress_rate = 20.0
        self._inf = Influence()                  # 热路径复用，不在帧里 new
        self._inf.instant.glucose = 6.0 * self._shots   # 落地下肚：立刻入血

    def _compute_influence(self, state: BioState, cfg: BioSimConfig, tick: Tick) -> Influence:
        self.remaining -= tick.dt
        self._inf.delta.energy = self._energy_rate
        self._inf.aspects["stress"] = max(0.0, state.stress - self._stress_rate * tick.dt)
        return self._inf

    def alive(self, state: BioState, cfg: BioSimConfig, tick: Tick) -> bool:
        return self.remaining > 0

    def on_expire(self, state: BioState, cfg: BioSimConfig, tick: Tick) -> None:
        state.set_aspect("last_coffee_shots", self._shots)
```

实测输出：

```plaintext
注册后的动作表: [..., "coffee"]              <- 自动进 available_actions
喝下 2 份: 血糖 55.0 -> 67.0 (instant) | 能量 80.0 -> 80.0 (此刻不动)
0.5h 后:   能量 91.0 (delta 生效)
1.1h 后:   效果已被 alive 回收, last_coffee_shots=2 (on_expire 已收尾)
cancel 后: 效果立刻移除, on_expire 照样走
```

---

## 8. 改东西 = 改哪里

| 想改什么 | 改哪里 |
| --- | --- |
| 加/删一个连续维度（体温、水合…） | `types/BioState.py` 的 `StateVec.elements` 与 `__init__`；`config.py` 里补 `<名>_initial / _min / _max` 三行。**引擎、观测层、向量运算都不用动** |
| 改某维度的上下限或初始值 | `config.py` |
| 增删 / 整组替换常驻模拟项 | 构造时传 `base_effects=(...)`，或改 `BioSimEngine.BASE_EFFECTS` |
| 加一个可下达的动作 | `actions.py`：Params + `@effect` + 效果类。要给模型用再去 `modules/tools/` 挂工具 |
| 改数值平衡 | `config.py` 一张表，没有散落的魔数 |
| 让**程序**读到新量 | `observation.py` 加字段 + `engine.observe()` 填上 |
| 让**模型**看到 | `modules/tools/impl/bio_text.py` 加占位符（文本是工具，不进核心） |
| 换掉整套读数的措辞 | 换掉 `bio_text` 就行，核心不认识它 |

维度的约定是 `<名>_initial / <名>_min / <名>_max`，引擎按约定 `getattr` 取用。**少写一个会在构造时就报 AttributeError**，不会静默跑飞。

---

## 9. 红线

1. `_compute_influence` 里**不许改状态** —— 声明，不执行。唯一例外是 `on_expire`。
2. **不许在热路径分配**：`self._inf` 建一次、复用；不 new 向量、不建临时 dict。
3. **不许阻塞**：它在锁内每帧跑，卡住就是整个引擎卡住。
4. `instant` 只在动作下达那一刻有效，引擎用完即清零，别指望它在后续帧还在。
5. `on_expire` 只有 `set_aspect` 这一条出口，**改不了连续维度**。想留下连续量的尾巴，得在 `alive` 还没为假的时候用 `delta` 办。
6. 效果之间**不要互相引用**，靠状态通信（`FullnessDynamics` 读血糖就是这么做的）。
7. `set_aspect` 什么名字都收，写错了不会报错，只是没人读 —— 名字要和 `BioState` 的注解对上。
8. `refusal` 要写就写成**同样签名的 classmethod**，不要 `refusal = classmethod(lambda ...)`：那是属性不是方法，静态检查对不上，参数也没有提示。

---

## 10. 内置效果

| 名字 | 参数 | control | 说明 |
| --- | --- | --- | --- |
| `energy` | — | AUTONOMIC | 常驻。按睡眠相位回/耗，血糖过低时额外掉 |
| `glucose` | — | AUTONOMIC | 常驻。持续消耗，睡眠时慢；低血糖时消耗减半 |
| `fullness` | — | AUTONOMIC | 常驻。**下降速度由血糖浓度决定**（高档 x0.35 / 常档 x1.0 / 低档 x1.6） |
| `stress` | — | AUTONOMIC | 常驻。能量低、饥饿、赖床时上升 |
| `mood` | — | AUTONOMIC | 常驻。向目标值收敛，目标由压力与能量决定 |
| `eat` | `EatParams(portion, quality)` | AUTONOMIC | **instant 充腹**，之后血糖/能量/心情随消化窗口上升 |
| `exercise` | `ExerciseParams(intensity, minutes)` | VOLITIONAL | 持续耗能、耗血糖，切 ACTIVE |
| `sleep` | `SleepParams()` | AUTONOMIC，可打断 | 驱动浅睡 / 深睡 / REM / 赖床的相位循环 |
| `wake` | `WakeParams()` | VOLITIONAL，一次性 | 立刻回到清醒 |

前五个就是 `BioSimEngine.BASE_EFFECTS`（常驻模拟项），后四个是可下达的动作。
