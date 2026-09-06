from pathlib import Path

from .VecTypes import Vector, Dimension


class StateVec(Vector):
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
    def __init__(self, *,
                 initial_state: StateVec,
                 delta_per_sec: StateVec | None = None,
                 ) -> None:
        self.current_state = initial_state
        self.delta_per_sec = delta_per_sec if delta_per_sec is not None else StateVec()

    @staticmethod
    def from_dict(data: dict) -> BioState:
        def build_state(mapping: dict) -> StateVec:
            vec = StateVec()
            for element in StateVec.elements:
                name = element.name
                if name in mapping:
                    setattr(vec, name, mapping[name])
            return vec

        def state_fields() -> dict[str, bool]:
            from typing import get_args, get_type_hints
            fields: dict[str, bool] = {}
            for name, hint in get_type_hints(BioState.__init__).items():
                if name == "return":
                    continue
                args = get_args(hint)
                candidates = [hint] if not args else list(args)
                if StateVec in candidates:
                    fields[name] = type(None) in candidates
            return fields

        kwargs: dict[str, StateVec] = {}
        for name, optional in state_fields().items():
            raw = data.get(name)
            if raw is None:
                if optional:
                    continue
                raise KeyError(f"missing required state field {name!r}")
            if not isinstance(raw, dict):
                raise TypeError(f"state field {name!r} must be a mapping")
            kwargs[name] = build_state(raw)
        return BioState(**kwargs)

    @staticmethod
    def from_json(json_text: str) -> BioState:
        import json
        return BioState.from_dict(json.loads(json_text))

    @staticmethod
    def from_json_file(path: Path) -> BioState:
        import json
        with open(path, "r", encoding="utf-8") as file:
            return BioState.from_json(file.read())