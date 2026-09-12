"""文本渲染层：给 LLM 的中文提示词 + 动作解析。"""
from __future__ import annotations

from .engine import BioSimEngine


class BioSimRenderer:
    _DIMENSION = {
        "energy": {"name": "能量", "low": "疲惫", "mid": "平静", "high": "充沛"},
        "fullness": {"name": "饱腹", "low": "饥饿", "mid": "尚可", "high": "饱满"},
        "mood": {"name": "心情", "low": "低落", "mid": "中性", "high": "愉悦"},
        "stress": {"name": "压力", "low": "轻松", "mid": "适中", "high": "高压"},
    }
    _LOOKS = {
        "sleep": {"awake": "清醒", "dozing": "赖床", "light_sleep": "浅睡",
                  "deep_sleep": "深睡", "rem": "REM"},
        "activity": {"rest": "休息", "moderate": "适度活动", "active": "活跃"},
        "hunger": {"full": "饱食", "content": "不饿", "hungry": "饥饿"},
        "mood": {"neutral": "中性", "happy": "愉悦", "irritable": "易怒"},
    }
    _SYNONYMS = {
        "eat": ["eat", "吃", "进食", "补给", "饭"],
        "exercise": ["exercise", "运动", "锻炼", "健身"],
        "sleep": ["sleep", "睡觉", "入睡", "睡", "休息", "躺"],
        "wake": ["wake", "醒", "起床", "叫醒", "醒来"],
    }

    def __init__(self, engine: BioSimEngine) -> None:
        self.engine = engine

    def actions(self) -> list[str]:
        return self.engine.available_actions()

    def render(self) -> str:
        s = self.engine.snapshot()
        lines = [
            f"当前时间 {int(s['time']):02d}:00，已进行 {s['elapsed_hours']:.1f} 模拟小时。",
            "状态：{0} · {1} · {2} · {3}".format(
                self._label("sleep", s["sleep"]), self._label("activity", s["activity"]),
                self._label("hunger", s["hunger"]), self._label("mood", s["mood"]),
            ),
            self._dim_line("energy", s["energy"]),
            self._dim_line("fullness", s["fullness"]),
            self._dim_line("mood", s["mood_score"]),
            self._dim_line("stress", s["stress"]),
            "",
            "请选择一个动作，回复它的英文名（如 eat / exercise / sleep / wake）：",
            "  " + " / ".join(self.actions()),
        ]
        return "\n".join(lines)

    def parse_action(self, reply: str) -> str | None:
        text = (reply or "").strip().lower()
        for action in self.actions():
            for word in self._SYNONYMS[action]:
                if word in text:
                    return action
        return None

    def describe(self) -> str:
        s = self.engine.snapshot()
        out = ["{0:02d}:00 · 第 {1:.1f} 小时".format(int(s["time"]), s["elapsed_hours"]),
               "睡眠:{0} 活动:{1} 饥饿:{2} 情绪:{3}".format(
                   self._label("sleep", s["sleep"]), self._label("activity", s["activity"]),
                   self._label("hunger", s["hunger"]), self._label("mood", s["mood"]))]
        for key, value in (("energy", s["energy"]), ("fullness", s["fullness"]),
                           ("mood", s["mood_score"]), ("stress", s["stress"])):
            out.append("{0}:{1:.0f}/100（{2}）".format(
                self._DIMENSION[key]["name"], value, self._qual(key, value)))
        return " | ".join(out)

    def _dim_line(self, key: str, value: float) -> str:
        return "{0}：{1:.0f}/100（{2}）".format(self._DIMENSION[key]["name"], value, self._qual(key, value))

    def _label(self, kind: str, member) -> str:
        value = member.value if hasattr(member, "value") else str(member)
        return self._LOOKS.get(kind, {}).get(value, value)

    def _qual(self, key: str, value: float) -> str:
        meta = self._DIMENSION[key]
        if value < 30:
            return meta["low"]
        if value < 70:
            return meta["mid"]
        return meta["high"]

