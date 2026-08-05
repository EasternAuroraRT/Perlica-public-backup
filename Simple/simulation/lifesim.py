import re
from typing import Dict, List, Optional, Callable, Any, Tuple
from dataclasses import dataclass, field
from copy import deepcopy

# ---------- 1. 核心数据结构 ----------
@dataclass
class Location:
    id: str
    name: str
    desc: str = "一片空地"
    exits: Dict[str, str] = field(default_factory=dict)  # {"方向": "目标地点id"}
    items: List[str] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)  # {"locked": True, "key": "铜钥匙"}

@dataclass
class Player:
    loc_id: str = "hall"
    inventory: List[str] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=lambda: {"hp": 100, "energy": 80, "mood": "calm"})
    flags: Dict[str, bool] = field(default_factory=dict)  # 剧情开关

@dataclass
class WorldSnapshot:
    time: str
    weather: str
    player: Player
    current_location: Location
    nearby_locations: Dict[str, str]  # 方向 -> 地点名
    available_actions: List[str]

# ---------- 2. 动作注册表（可扩展核心） ----------
class ActionRegistry:
    _actions: Dict[str, Callable] = {}

    @classmethod
    def register(cls, name: str):
        """装饰器：注册动作处理函数"""
        def decorator(func):
            cls._actions[name] = func
            return func
        return decorator

    @classmethod
    def get(cls, name: str) -> Optional[Callable]:
        return cls._actions.get(name)

    @classmethod
    def list_actions(cls) -> List[str]:
        return list(cls._actions.keys())

# ---------- 3. 世界引擎主体 ----------
class WorldEngine:
    def __init__(self):
        # 数据容器
        self.locations: Dict[str, Location] = {}
        self.player = Player()
        self.time = "2026-08-04 09:00"
        self.weather = "晴朗"
        self.global_vars: Dict[str, Any] = {}  # 世界级变量
        
        # 钩子系统
        self.pre_hooks: Dict[str, List[Callable]] = {}
        self.post_hooks: Dict[str, List[Callable]] = {}
        
        # 内置初始化
        self._build_default_world()

    # ---------- 地图构建 ----------
    def _build_default_world(self):
        self.add_location(Location(
            id="hall", name="大厅", desc="宽敞的大厅，挂着古老的水晶灯。",
            exits={"北": "kitchen", "东": "study", "南": "garden"}
        ))
        self.add_location(Location(
            id="kitchen", name="厨房", desc="飘着淡淡的麦香，灶台还温热。",
            exits={"南": "hall"},
            items=["面包", "铜钥匙"]
        ))
        self.add_location(Location(
            id="study", name="书房", desc="书架上摆满旧书，墙角有一扇紧锁的铁门。",
            exits={"西": "hall", "下": "vault"},
            properties={"has_vault": True}
        ))
        self.add_location(Location(
            id="garden", name="花园", desc="玫瑰盛开，喷泉在阳光下闪烁。",
            exits={"北": "hall"}
        ))
        self.add_location(Location(
            id="vault", name="密室", desc="布满灰尘，墙上刻着奇怪的符号。",
            exits={"上": "study"},
            properties={"requires_key": "铜钥匙"}  # 门禁条件
        ))
        self.player.loc_id = "hall"

    def add_location(self, loc: Location):
        self.locations[loc.id] = loc

    def get_location(self, loc_id: str) -> Optional[Location]:
        return self.locations.get(loc_id)

    def _require_location(self, loc_id: str) -> Location:
        loc = self.locations.get(loc_id)
        if loc is None:
            raise KeyError(f"unknown location: {loc_id}")
        return loc

    # ---------- 核心交互接口（对外统一） ----------
    def step(self, raw_input: str) -> Dict[str, Any]:
        """
        统一入口：接收自然语言指令，返回结构化观察结果。
        返回值格式：
        {
            "messages": [str],          # 反馈信息列表
            "snapshot": dict,           # 完整状态快照（可序列化）
            "available_actions": [str], # 当前可行的指令列表
            "events": [str]             # 触发的钩子事件
        }
        """
        # 1. 解析指令
        cmd, target = self._parse_command(raw_input)
        
        # 2. 执行前置钩子
        self._run_hooks("pre", cmd, target)
        
        # 3. 查找并执行动作
        handler = ActionRegistry.get(cmd)
        if not handler:
            return self._build_observation([f"未知指令: {cmd}"], events=["unknown_command"])
        
        # 执行动作（返回消息列表、是否拦截后续逻辑）
        messages, intercepted = handler(self, target)
        if not isinstance(messages, list):
            messages = [messages]
        
        # 4. 执行后置钩子
        self._run_hooks("post", cmd, target)
        
        # 5. 构建观察结果
        return self._build_observation(messages, intercepted=intercepted)

    def _parse_command(self, raw: str) -> Tuple[str, str]:
        """简单解析器：提取动词和宾语"""
        parts = raw.strip().split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        target = parts[1] if len(parts) > 1 else ""
        # 处理复合命令如 'take key' -> cmd='take', target='key'
        return cmd, target

    def _build_observation(self, messages: List[str], intercepted: bool = False, events: List[str] | None = None) -> Dict:
        loc = self._require_location(self.player.loc_id)
        snapshot = {
            "time": self.time,
            "weather": self.weather,
            "player": {
                "loc_id": self.player.loc_id,
                "inventory": self.player.inventory.copy(),
                "attributes": self.player.attributes.copy(),
                "flags": self.player.flags.copy()
            },
            "current_location": {
                "id": loc.id,
                "name": loc.name,
                "desc": loc.desc,
                "items": loc.items.copy(),
                "exits": loc.exits.copy()
            }
        }
        # 动态生成可用动作（仅展示相关动作）
        available = ["look", "inventory", "status"]
        available.extend([f"go {d}" for d in loc.exits.keys()])
        if loc.items:
            available.extend([f"take {item}" for item in loc.items])
        if self.player.inventory:
            available.extend([f"drop {item}" for item in self.player.inventory])
            available.append("use")  # 具体使用时再解析
        
        return {
            "messages": messages,
            "snapshot": snapshot,
            "available_actions": available,
            "events": events or []
        }

    # ---------- 钩子系统 ----------
    def register_hook(self, hook_type: str, action_name: str, callback: Callable):
        """hook_type: 'pre' 或 'post'"""
        key = f"{hook_type}:{action_name}"
        if key not in self.pre_hooks:
            self.pre_hooks[key] = []
        self.pre_hooks[key].append(callback)

    def _run_hooks(self, hook_type: str, action_name: str, target: str):
        key = f"{hook_type}:{action_name}"
        for hook in self.pre_hooks.get(key, []):
            hook(self, action_name, target)

    def _advance_time(self, minutes: int):
        from datetime import datetime, timedelta
        dt = datetime.strptime(self.time, "%Y-%m-%d %H:%M")
        dt += timedelta(minutes=minutes)
        self.time = dt.strftime("%Y-%m-%d %H:%M")

# ---------- 4. 内置动作注册（通过装饰器） ----------
@ActionRegistry.register("go")
def action_go(engine: WorldEngine, target: str) -> Tuple[List[str], bool]:
    loc = engine._require_location(engine.player.loc_id)
    if target not in loc.exits:
        return [f"从{loc.name}无法前往{target}"], True
    
    target_id = loc.exits[target]
    target_loc = engine._require_location(target_id)
    
    # 检查门禁条件
    if target_loc.properties.get("requires_key"):
        need_key = target_loc.properties["requires_key"]
        if need_key not in engine.player.inventory:
            return [f"{target_loc.name}的门锁着，需要{need_key}"], True
    
    # 执行移动
    engine.player.loc_id = target_id
    engine.player.attributes["energy"] = max(0, engine.player.attributes["energy"] - 5)
    # 自动推进时间（15分钟）
    engine._advance_time(15)
    return [f"你走向{target}，来到了{target_loc.name}。{target_loc.desc}"], True

@ActionRegistry.register("take")
def action_take(engine: WorldEngine, target: str) -> Tuple[List[str], bool]:
    loc = engine._require_location(engine.player.loc_id)
    if target not in loc.items:
        return [f"这里没有{target}"], True
    if target in engine.player.inventory:
        return [f"你已拥有{target}"], True
    
    loc.items.remove(target)
    engine.player.inventory.append(target)
    return [f"你捡起了{target}"], True

@ActionRegistry.register("drop")
def action_drop(engine: WorldEngine, target: str) -> Tuple[List[str], bool]:
    if target not in engine.player.inventory:
        return [f"你身上没有{target}"], True
    engine.player.inventory.remove(target)
    loc = engine._require_location(engine.player.loc_id)
    loc.items.append(target)
    return [f"你放下了{target}"], True

@ActionRegistry.register("look")
def action_look(engine: WorldEngine, _: str) -> Tuple[List[str], bool]:
    loc = engine._require_location(engine.player.loc_id)
    msg = f"你在{loc.name}。{loc.desc}\n"
    if loc.items:
        msg += f"你能看到: {', '.join(loc.items)}\n"
    if loc.exits:
        msg += f"出口: {', '.join(loc.exits.keys())}"
    return [msg], True

@ActionRegistry.register("inventory")
def action_inventory(engine: WorldEngine, _: str) -> Tuple[List[str], bool]:
    inv = engine.player.inventory
    if not inv:
        return ["背包空空如也。"], True
    return [f"背包: {', '.join(inv)}"], True

@ActionRegistry.register("status")
def action_status(engine: WorldEngine, _: str) -> Tuple[List[str], bool]:
    p = engine.player
    loc = engine._require_location(p.loc_id)
    return [
        f"时间: {engine.time} | 天气: {engine.weather}",
        f"位置: {loc.name}",
        f"体力: {p.attributes['energy']}% | 心情: {p.attributes.get('mood', '平静')}",
        f"背包: {', '.join(p.inventory) if p.inventory else '空'}"
    ], True

@ActionRegistry.register("use")
def action_use(engine: WorldEngine, target: str) -> Tuple[List[str], bool]:
    # 示例：用钥匙开门
    if target == "铜钥匙" and engine.player.loc_id == "study":
        vault = engine._require_location("vault")
        if "requires_key" in vault.properties:
            del vault.properties["requires_key"]
            engine.player.inventory.remove("铜钥匙")
            return ["你插入铜钥匙，铁门发出沉闷的咔嗒声，打开了！"], True
    return [f"不知道如何使用{target}"], True

# ---------- 6. 扩展示例：如何添加自定义动作 ----------
@ActionRegistry.register("search")
def action_search(engine: WorldEngine, target: str) -> Tuple[List[str], bool]:
    loc = engine._require_location(engine.player.loc_id)
    if not target:
        # 搜索当前房间
        hidden_items = {"hall": ["旧报纸"], "garden": ["小石子"]}
        found = hidden_items.get(loc.id, [])
        for item in found:
            if item not in loc.items:
                loc.items.append(item)
        return [f"你仔细搜索了一遍，发现了：{', '.join(found) if found else '什么也没有'}。"], True
    return ["搜索什么？"], True

# ---------- 7. 交互演示（纯命令行测试） ----------
if __name__ == "__main__":
    engine = WorldEngine()
    print("=== 世界引擎已启动（纯逻辑，无AI） ===")
    obs = engine.step("look")
    print("\n".join(obs["messages"]))
    print(f"\n可用动作: {', '.join(obs['available_actions'][:5])}...")
    
    while True:
        cmd = input("\n> ").strip()
        if cmd in ["exit", "quit"]:
            break
        if cmd == "?":
            print("可用动作:", ", ".join(ActionRegistry.list_actions()))
            continue
        
        obs = engine.step(cmd)
        print("\n--- 反馈 ---")
        for msg in obs["messages"]:
            print(msg)
        print("--- 状态快照（缩略） ---")
        print(f"位置: {obs['snapshot']['current_location']['name']}")
        print(f"背包: {obs['snapshot']['player']['inventory']}")
        print(f"体力: {obs['snapshot']['player']['attributes']['energy']}")