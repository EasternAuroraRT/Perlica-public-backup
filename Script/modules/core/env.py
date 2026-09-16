from __future__ import annotations
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
import json
import os
from datetime import datetime
from pathlib import Path

import napcat as np
from config import config
from .chatwindow import ChatWindow, chat_type_str
from .logger import log
from ..simulation import biosim as bio
from ..tools.impl import alarm

# ================== 全局状态 ==================
npclient: np.NapCatClient
self_id: int
self_name: str
chatwindows: dict[ChatWindow.ChatType, dict[str, ChatWindow]] = {}
active_chatwindow: ChatWindow
sysprompt: list[ChatCompletionMessageParam] = [] # Do not easily read or write this variant -- unless you know what you are doing!
chat_log: list[ChatCompletionMessageParam] = []
no_disturb_mode: bool = False
pending_event_msgs: list[ChatCompletionContentPartParam] = []   # 睡着/静音期间积压的事件消息
username_list: dict[int, str] = {}
biosim_engine: bio.BioEngine
__initialized: bool = False

# ==================== 常量 ====================
settings_folder: Path = Path(__file__).parent.parent / "settings"
chat_log_dir: Path = Path("./working_cache/chat/")
CHAT_LOG_FILE: str = "chat_log.json"
bio_engine_checkpoint_path: Path = Path("./working_cache/biosim/")


# ==================== 接口 ====================
def take_pending() -> list[ChatCompletionContentPartParam]:
    """取走积压的事件消息（取完即清空）。"""
    taken = pending_event_msgs.copy()
    pending_event_msgs.clear()
    return taken


def save_chat_log(dirpath: str | Path = chat_log_dir) -> Path:
    """把当前对话记录原子写盘（退出时由 main 调用）。"""
    directory = Path(dirpath)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / CHAT_LOG_FILE
    temp = target.with_name(target.name + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(chat_log, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    log.info(f"[env] Chat history (item counts: {len(chat_log)}) saved: {target}")
    return target


def get_chatwindow_prompt() -> str:
    window = active_chatwindow
    if not window:
        return "当前未激活聊天窗口"
    return f'''当前激活聊天窗口：
{chat_type_str[window.chat_type]}`{window.name}`({window.chat_id})
'''


def is_active_time() -> bool:
    return biosim_engine.get_slice().sleep is bio.SleepState.AWAKE


# ==================== Init ====================
async def init() -> None:
    global npclient, self_id, self_name, chatwindows, active_chatwindow
    global sysprompt, chat_log, username_list, biosim_engine, __initialized

    def now_clock_hour() -> float:
        """当前时刻的小数小时（0~24），精确到分钟。"""
        now = datetime.now()
        return now.hour + now.minute / 60.0

    def load_chat_log() -> list[ChatCompletionMessageParam]:
        target = chat_log_dir / CHAT_LOG_FILE
        if not target.exists():
            return []
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        log.info(f"[env] Chat history loaded with {len(payload)} items.")
        return payload if isinstance(payload, list) else []

    alarm.ensure_loaded()

    npclient = np.NapCatClient(ws_url=config.np_ws_url, token=config.np_token)
    if not __initialized:
        biosim_engine = bio.BioEngine(
            start_clock_hour=now_clock_hour(),      # 只在没有存档时用得上
            integration_step_hours=0.05,            # 积分精度
            sim_hours_per_real_hour=1.0,            # 模拟缩放
            checkpoint=bio_engine_checkpoint_path,
        )
        if not biosim_engine.loaded_from_checkpoint:
            biosim_engine.add_effects(*bio.baseline_effects())
        chat_log = load_chat_log()
        __initialized = True

    async with npclient:
        chatwindows = {
            ChatWindow.ChatType.Private: {},
            ChatWindow.ChatType.Group: {},
            ChatWindow.ChatType.Unknown: {'void': ChatWindow(ChatWindow.ChatType.Unknown, 'void', 'void')},
        }
        active_chatwindow = chatwindows[ChatWindow.ChatType.Unknown]['void']

        # Sys-prompt
        role_prompt: str = ''
        with open(settings_folder / "settings.json", "r", encoding="utf-8") as setting_files:
            settings = json.load(setting_files)
            assert isinstance(settings, list)
            for setting in settings:
                assert isinstance(setting, dict)
                for name, path in setting.items():
                    role_prompt += f"\n{name}\n" + (settings_folder / path).read_text(encoding="utf-8") + '\n'

        # Self
        self_info = await npclient.get_login_info()
        self_id = self_info.get("user_id")
        self_name = self_info.get("nickname")
        log.info(f"Self: id={self_id}, nickname={self_name}")

        # Private Chat
        friend_list = await npclient.get_friend_list()
        username_list = {}
        if not friend_list:
            log.warning("Cannot get friend list!")
        else:
            log.info(f"Friends count: {len(friend_list)}")
            role_prompt += "\n私聊列表:\n"
        for friend in friend_list:
            user_id = friend.get("user_id")
            user_nickname = friend.get("nickname")
            username_list[user_id] = user_nickname
            if user_id == self_id:
                continue
            role_prompt += f"id = {user_id}\tnickname = {user_nickname}\n"
            chatwindows[ChatWindow.ChatType.Private][str(user_id)] = ChatWindow(ChatWindow.ChatType.Private, str(user_id), user_nickname)

        # Group Chat
        group_list = await npclient.get_group_list()
        if not group_list:
            log.warning("Cannot get group list!")
        else:
            log.info(f"Groups count: {len(group_list)}")
            role_prompt += "\n群聊列表:\n"
        for group in group_list:
            group_id = str(group.get("group_id"))
            group_name = group.get('group_name')
            role_prompt += f"id = {group_id}\tname = {group_name}\n"
            chatwindows[ChatWindow.ChatType.Group][group_id] = ChatWindow(ChatWindow.ChatType.Group, group_id, group_name)

        role_prompt += "\nCurrently not in any chat window.\n"
        sysprompt = [{"role": "system", "content": role_prompt}]
        log.debug(f"[env] Starting BioSim:\n{biosim_engine.get_slice()}")
        from modules.tools.impl import bio_text
        log.info(f"Current state: \n{bio_text.state_text(biosim_engine)}\n")

def clean_up() -> None:
    if not __initialized:
        return
    try:
        biosim_engine.save_checkpoint()
    except Exception as e:
        log.error(f"[env] Failed to save biosim checkpoint: {e}")
    try:
        save_chat_log()
    except Exception as e:
        log.error(f"[env] Failed to save chat log: {e}")
    log.info("[env] Clean-up done.")