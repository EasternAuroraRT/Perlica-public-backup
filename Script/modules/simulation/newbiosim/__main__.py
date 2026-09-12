"""示例：手动推进 + 动作 + 文本渲染。"""
from .config import default_config
from .engine import BioSimEngine
from .render import BioSimRenderer
from .actions import EatParams, ExerciseParams, SleepParams, WakeParams


def main():
    cfg = default_config(
        sleep_full_duration=3.0, doze_timeout=0.2,
        energy_awake_cost=8.0, energy_sleep_recover=15.0,
        fullness_decay_per_hour=40.0, exercise_cooldown=0.4,
        stress_rise_rate=30.0, stress_fall_rate=8.0,
        stress_irritable_threshold=60.0, stress_happy_threshold=25.0,
        stress_happy_energy_required=50.0, stress_energy_low_threshold=50.0,
        digest_duration=0.5, digest_fullness_gain=60.0,
    )
    engine = BioSimEngine(config=cfg, start_hour=8.0)
    renderer = BioSimRenderer(engine)
    engine.advance(4.0)
    print(renderer.render())
    print()

    commands = {"eat": EatParams, "exercise": ExerciseParams,
                "sleep": SleepParams, "wake": WakeParams}
    for reply in ["我吃点东西", "运动一下", "去睡觉", "起床", "休息一下"]:
        engine.advance(1.0)
        action = renderer.parse_action(reply)
        if action:
            try:
                engine.act(commands[action]())
            except ValueError as exc:
                print(f"[{action}] 被拒：{exc}")
        print("--- ", renderer.describe(), "| 可用:", engine.available_actions())


if __name__ == "__main__":
    main()

