from pathlib import Path

from .VecTypes import Vector, Dimension
from ..bio_enums import SleepState, ActivityLevel


class StateVec(Vector):
    """生物状态的连续维度向量：energy / fullness / mood。"""
    elements = [
        Dimension("energy"),
        Dimension("fullness"),
        Dimension("mood"),
    ]
    def __init__(self, *,
                 energy: float = 0,
                 fullness: float = 0,
                 mood: float = 0,
                 ) -> None:
        super().__init__()
        self.energy = energy
        self.fullness = fullness
        self.mood = mood


class BioState:
    """可配置的生物状态容器。

    current_state   —— 连续维度的当前值（StateVec）。
    delta_per_sec   —— 连续维度每模拟秒的变化率（StateVec）。
    **aspects       —— 离散状态数据与枚举，按关键字配置（如 sleep / activity /
                       stress / clock_hour …）。它们是“相位层”，与连续维度一起构成完整状态。

    所以 BioState 的关注点是“这个角色身上有哪些数据与枚举”以及它们的当前取值，
    而不仅仅是某个连续向量。引擎负责推进 current_state 并驱动 aspects。

    方面字段已经在下方以类型注解声明，Pylance 等类型检查器能直接看到；
    通过 set_aspect 配置后，这些字段即带上声明的类型。
    """

    # 连续维度
    current_state: StateVec
    delta_per_sec: StateVec

    # 离散相位（枚举）
    sleep: SleepState
    activity: ActivityLevel

    # 连续标量方面
    stress: float
    clock_hour: float
    elapsed_hours: float
    sleep_duration: float
    doze_timer: float
    exercise_timer: float

    def __init__(self, *,
                 initial_state: StateVec,
                 delta_per_sec: StateVec | None = None,
                 **aspects,
                 ) -> None:
        self.current_state = initial_state
        self.delta_per_sec = delta_per_sec if delta_per_sec is not None else StateVec()
        self._aspect_names: tuple[str, ...] = ()
        for name, value in aspects.items():
            self.set_aspect(name, value)

    # ---------- aspects（数据 / 枚举） ----------
    def set_aspect(self, name: str, value) -> None:
        """配置或更新一个方面（数据或枚举成员）。"""
        if name not in self._aspect_names:
            self._aspect_names += (name,)
        setattr(self, name, value)

    @property
    def aspect_names(self) -> tuple[str, ...]:
        return self._aspect_names

    def aspect(self, name: str):
        """读取一个方面的当前值。"""
        return getattr(self, name)

    # ---------- 序列化 ----------
    def as_dict(self) -> dict:
        data: dict = {
            "current_state": {d.name: getattr(self.current_state, d.name) for d in StateVec.elements},
            "delta_per_sec": {d.name: getattr(self.delta_per_sec, d.name) for d in StateVec.elements},
        }
        for name in self._aspect_names:
            value = getattr(self, name)
            data[name] = value.value if hasattr(value, "value") else value
        return data

    @staticmethod
    def from_dict(data: dict) -> BioState:
        def build(vec_name: str) -> StateVec:
            raw = data.get(vec_name, {})
            vec = StateVec()
            for d in StateVec.elements:
                if d.name in raw:
                    setattr(vec, d.name, raw[d.name])
            return vec
        aspects = {k: v for k, v in data.items()
                   if k not in ("current_state", "delta_per_sec")}
        return BioState(initial_state=build("current_state"),
                        delta_per_sec=build("delta_per_sec"),
                        **aspects)

    def to_json(self) -> str:
        import json
        return json.dumps(self.as_dict(), ensure_ascii=False)

    @staticmethod
    def from_json(text: str) -> BioState:
        import json
        return BioState.from_dict(json.loads(text))

    def to_json_file(self, path: Path) -> None:
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.as_dict(), f, ensure_ascii=False, indent=2)

    @staticmethod
    def from_json_file(path: Path) -> BioState:
        import json
        with open(path, "r", encoding="utf-8") as f:
            return BioState.from_json(f.read())

    def __repr__(self) -> str:
        extra = ", ".join(f"{n}={getattr(self, n)!r}" for n in self._aspect_names)
        body = f"StateVec(current_state={self.current_state!r})"
        return f"{type(self).__name__}({body}{', ' + extra if extra else ''})"

