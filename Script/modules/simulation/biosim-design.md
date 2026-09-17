# biosim 系统设计

> 描述 biosim 的**当前实现**：分层、时间模型、状态模型、推进顺序、存档、单位，
> 以及一组已经拍板的设计决策（第 8 节）、仍然可疑的点（第 9 节）、
> 尚未定的类型系统重构（第 10 节）。
> 效果插件怎么写看 [biosim-effect.md](biosim-effect.md)；这份讲骨架。

---

## 0. 一句话

一个"效果只声明、引擎独占状态与时间"的连续时间模拟：

- 效果插件声明"这一帧想让状态怎么变"；
- 引擎是唯一的积分器、状态所有者和时间所有者；
- 时间是挂钟算出来的，引擎只负责把模拟推到"现在应该在的地方"。

## 1. 分层与边界

| 层 | 入口 | 做什么 | 明确不做 |
| --- | --- | --- | --- |
| 引擎 | `BioEngine` | 挂/摘效果、按精度积分、钳制、按真实时间同步、出读模型、存档 | 不认识业务、不认识动作、不认识措辞 |
| 类型 | `types/` | `StateVec` / `Aspects` / `AspectPatch` / `Influence` / `Tick` | 不含逻辑 |
| 效果 | `Effect` + `BasicEffects/` | 声明变化率 / 乘区 / 瞬时 / 方面 | 不直接改状态（没有例外） |
| 读模型 | `EngineSlice` | 类型化只读快照 | 措辞 |
| 消费方 | `main.py` / `tools/impl/bio_text.py` / `tools/__init__.py` | 读、渲染、挂效果 | 不碰引擎内部 |

**没有独立的时钟类**：时间行为由引擎自己的两个参数唯一确定，构造引擎就是全部 API。

## 2. 时间模型

只有两个模拟概念：**精度**与**缩放**。没有"时钟步长"这种东西。

| 名字 | 单位 | 含义 | 归谁 |
| --- | --- | --- | --- |
| `integration_step_hours` | 模拟小时 | 积分精度：把区间切成多细 | 引擎 |
| `sim_hours_per_real_hour` | 模拟小时 / 真实小时 | 缩放：真实时间到模拟时间的倍率 | 引擎 |

**时间是挂钟算的，不自己累计**。引擎记一个"上次同步的真实时刻"
`_last_sync_unix_seconds`，在**读取 / 改状态 / 存档前**自动 `sync()`：

```plaintext
due_hours = (now - _last_sync_unix_seconds) * sim_hours_per_real_hour / 3600
_last_sync_unix_seconds = now
advance(due_hours)
```

所以：

- **读取时更新**：`get_slice()` 先同步到此刻再读数，读数永远对得上挂钟；
- **`advance(sim_hours)`**：按 `integration_step_hours` 走整步，最后不足一步的零头按实际时长结算。
  效果器 `_step` 收到的 `dt` 就是真实推进量，它不需要知道"多久被调一次"；
- **退出 = 结算**：`save_checkpoint()` 内部先同步到此刻，再切片落盘；
- **读档补算**：存档带 `saved_at_unix_seconds` 与缩放；同日补 `(now - saved_at) * 缩放`；
  **跨天直接丢弃存档、当新的一天**（`loaded_from_checkpoint = False`）。

`dt` 的精度是"合同式"的：一个时间片大约是 `integration_step_hours`，最后一片是零头；
效果器按拿到的实际 `dt` 计算，不假设它恒等于某个值。

## 3. 状态模型

**连续维度**（`StateVec`）：每个维度把"范围 / 初值 / 读数档位"声明在自己身上（`Dimension`）。
当前：`energy` / `fullness` / `mood` / `glucose`，都是 0~100。

**方面表**（`Aspects`）：不连续的标量与枚举——`sleep` / `activity` / `stress` /
`clock_hour` / `elapsed_hours` / `sleep_duration_hours` / `last_sleep_duration_hours` /
`digest_left_hours` / `exercise_left_hours`。

`AspectPatch` 是效果能提交的**部分**方面（`None` = 不动）；它和 `Aspects` 字段一一对应，
运行期有断言。（**这一层是待重构的 AI 实现，见第 10 节**。）

**效果声明**（`Influence`）：`delta_per_hour`（每小时变化率，相加）、`mul`（倍率增量，`(1+x)` 连乘）、
`instant`（落地那一次的点数跳变）、`aspects`（部分方面）。

`influence()` 是**纯查询**：引擎问一次，效果返回一份**新的** `Influence`；没有可复用的
可变缓冲、没有收尾函数。效果对象自己只持有构造参数与随机源。

## 4. 一次推进的顺序

`BioEngine._step(dt_hours)`：

1. `tick = (dt_hours, clock_hour, elapsed_hours)`；
2. 归零 `net`，把 `mul` 填成单位元 1.0；
3. 依次问每个效果 `influence(state, tick)`：
   `net += delta_per_hour`；`mul.multiply_by_shifted(inf.mul)`；`inf.aspects.apply_to(aspects)`；
4. `net *= mul`，再 `current_state.add_scaled(net, dt_hours)`（前向欧拉）；
5. `_clamp_state()` 把连续维度夹回范围；
6. `clock_hour += dt_hours (mod 24)`、`elapsed_hours += dt_hours`；
7. `_reap()`：`alive` 为假的效果直接移除（终态在它最后一帧的 `influence` 里声明过）。

挂载（`add_effect`）：同步到此刻 → `refusal(读模型)` 过闸 → `influence` 落地（`dt=0`）→
`instant` 应用一次 → 入列 → 回收一遍。

## 5. 存档与恢复

- 文件：`working_cache/biosim/checkpoint.pkl`；
- 形状：开头一行**魔数 + 版本头**（`biosim-checkpoint v2`），后面是 pickle 的
  `CheckpointPayload`（`format` / `state` / `effects` / `saved_at_unix_seconds` / `sim_hours_per_real_hour`）；
- 写入：同步到此刻 → **锁内切片**（`header + pickle.dumps` 到内存）→ **锁外落盘**
  （`.tmp` → `fsync` → `os.replace`），IO 不占状态锁；
- `saved_at` 取的就是同步那一刻，和状态是同一时刻；
- 读取：缺文件 / 跨天 → `False`（当新的一天）；同日 → 补算停机段；
- **存档是不可信输入**，版本号只证明格式代际，不保证内容没坏/没被塞私货。所以：
  - 反序列化走白名单 `_SafeUnpickler`：只放行 `builtins` / `random` / `biosim` 自己的类，
    别的直接判坏档 —— 堵死 pickle 的任意代码执行；
  - 载入后做结构校验：`state` 必须是 `BioState`、`effects` 必须是效果对象、
    时间/缩放必须是数字、枚举必须合法、数值必须有限；越界数值统一 `_clamp_state()` 夹回；
  - **版本不认识 / 内容读坏 / 校验不过 → 不抛给调用方崩掉**：`__init__` 记 `load_error`、
    当新的一天，退出时写回新档自愈；
- `CHECKPOINT_FORMAT` 在 schema 变化（改字段名/改效果结构）时必须 +1；
- `loaded_from_checkpoint` 是"是否真的恢复了"的正式判据（不要用"效果列表是否为空"）。

## 6. 单位与命名

- 时间一律 `*_hours`（模拟小时）；真实秒 `*_seconds` / `*_unix_seconds`；
- 速率一律 `*_per_hour`；
- 瞬时量 `*_gain` 是点数；
- 无量纲：`portion` / `quality` / `intensity` / `factor` / `*_low` / `*_stress` 阈值。

## 7. 文件地图

```plaintext
biosim/
  BioEngine.py        引擎（时间 / 积分 / 挂摘 / 读模型 / 存档）
  EngineSlice.py      类型化读模型
  Effect.py           效果契约（只声明，无收尾函数）
  BioEnum.py          枚举
  __main__.py         手跑示例
  types/
    Vector.py         维度声明 + 向量
    BioState.py       StateVec / Aspects / AspectPatch / BioState
    Influence.py      delta_per_hour / mul / instant / aspects
    Tick.py           (dt_hours, clock_hour, elapsed_hours)
  BasicEffects/
    physiology.py     常态（能量 / 血糖 / 饱腹 / 压力 / 心情）+ baseline_effects()
    physics.py        吃 / 运动 / 睡 / 醒（+ sleep_effect 种子注入）
```

---

## 8. 设计决策（已拍板，别再当问题提）

1. **时间归引擎，不单开时钟类**。行为由"精度 + 缩放"唯一确定，多一个对象只是多一层转发。
2. **只有一个模拟精度参数**（`integration_step_hours`）。非线性怎么积、区间怎么切，是引擎自己的事；
   引擎按这个步长细分，效果器只拿到实际 `dt`。
3. **读取时同步是有意的**。`dt` 是合同式的"大约一个时间片"，不承诺恒定；读取顺手把时间追平，
   状态永远对得上挂钟。
4. **跨天重置是有意的**。中间的外界事件（吃饭、睡觉、出门）不该被"补"出来；
   补出来的值才是真的错，用户一眼就能看出不对。
5. **效果全程声明式，没有例外**。`on_expire` 已删除；`influence` 是纯查询，每次返回新声明。
6. **效果不"自己算最终值"**：整合、钳制连续维度、推进时钟都是引擎的事。
7. **框架不做数值兜底**：乘区下限之类的约束是数值设计的事，不该由引擎替它挡。
8. **参数紧挨着它配置的东西**：没有全局 config；整套平衡是一个返回效果列表的普通函数。
9. **算法/实现层面的小瑕疵按实现改**，不上升到设计（例如 `saved_at` 的取值时刻）。
10. **测试爱加不加**：`__main__.py` 是示例，不是测试套件。
11. **静态可见性不容取舍**：类型检查器必须能看到字段；同时修改点尽量唯一。

## 9. 仍然可疑的点

> 只留我仍然想不通、且不属于"已拍板"和"待重构类型系统"的。

### 9.1 引擎为了出读模型依赖具体效果模块（smell）

`BioEngine` 直接 `from .BasicEffects.physiology import hunger_of, mood_of, glycemia_of`。
"引擎不认识业务"这句话就打了折；加一个带档位的维度要动 `StateVec` + `physiology` + `EngineSlice` 三处。
更彻底的做法是把"连续值 → 枚举"也声明在维度上。

### 9.2 方面没有统一的范围/约束声明（smell）

连续维度的范围由 `Dimension` 声明、引擎统一钳制；方面的 `stress` 却由 `StressDynamics` 自己 `min/max`。
范围属于字段自己的性质，宜像 `Dimension` 那样声明在字段上，由引擎统一钳。

### 9.3 pickle 存档（取舍）

读档即执行任意代码；效果对象按模块限定名序列化，改类名/挪文件会让旧档直接读不回；
`random.Random` 状态也一起存。单机自用可接受，代价要知情。

## 10. 待定：类型系统重构

这一节记录一个**尚未拍板**的重构。要求（来自设计意图）：

- 整个体系里**只有 `Vector` 一种类型**；`Aspects` / `AspectPatch` / `Influence` 都是
  "动态但可检查属性"的实现，不是新类型概念；
- **动态 = 类内部属性不写死，但在类创建时（运行前）就确定**，不是运行时才长出来；
- **静态可见性不容取舍**：类型检查器要能看到字段（`vec.energy` 就是 `float`）；
- **修改点唯一**：加一个字段只改一处，最好连引擎/读模型/序列化都不用动；
- 效果声明（`Influence.aspects`）不能是另一个手写重复的字段表（`AspectPatch` 是 AI 屎山）。

核心矛盾：Python 里"运行期生成字段"与"静态检查器可见"天然冲突。只有把字段写进类体
（字面声明）才能被静态看到；要"只写一处"，就只能在类体里用带注解的声明 + 描述符/元类
把存储、校验、合并、读模型一条龙派生出来。

可能的方向（待定）：

1. **描述符字段 + 显式类体**：`energy: float = Dimension(...)`，类创建时收集字段、生成存储与运算；
   静态看到 `float`，改字段只加一行。
2. **`Aspects` 兼作声明**：字段带"未设置"哨兵，效果只填关心的字段；状态由引擎保证全部已设，
   消费方走 `EngineSlice`。去掉 `AspectPatch`，但状态字段类型会带上"未设置"。
3. 其他（由设计者指定）。
