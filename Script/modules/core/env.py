from __future__ import annotations
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
import json
import asyncio
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
chat_log: ChatLog
no_disturb_mode: bool = False
pending_event_msgs: list[ChatCompletionContentPartParam] = []   # 睡着/静音期间积压的事件消息
username_list: dict[int, str] = {}
biosim_engine: bio.BioEngine
_main_loop: asyncio.AbstractEventLoop
__initialized: bool = False

# ==================== 常量 ====================
settings_folder: Path = Path(__file__).parent.parent / "settings"
chat_log_dir: Path = Path("./working_cache/chat/")
CHAT_LOG_FILE: str = "chat_log.json"
bio_engine_checkpoint_path: Path = Path("./working_cache/biosim/")


# ==================== ChatLog ====================
class ChatLog:
    def __init__(self, dirpath: str | Path = chat_log_dir) -> None:
        self._dirpath = Path(dirpath)
        self._messages: list[ChatCompletionMessageParam] = self._load()

    def content(self) -> list[ChatCompletionMessageParam]:
        return self._messages

    def replace(self, messages: list[ChatCompletionMessageParam]) -> None:
        self._messages = messages

    def save(self) -> Path:
        """原子写盘：写不了就抛（由调用方兜），失败时清掉半个 `.tmp`，原文件不动。"""
        self._dirpath.mkdir(parents=True, exist_ok=True)
        target = self._dirpath / CHAT_LOG_FILE
        temp = target.with_name(target.name + ".tmp")
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(self._messages, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        log.info(f"[env] Chat history (item counts: {len(self._messages)}) saved: {target}")
        return target

    def _load(self) -> list[ChatCompletionMessageParam]:
        target = self._dirpath / CHAT_LOG_FILE
        if not target.exists():
            return []
        try:
            with open(target, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            log.error(f"[env] Chat history unreadable, ignored: {e}")
            return []
        if not isinstance(payload, list):
            log.error("[env] Chat history is not a list, ignored.")
            return []
        valid = [item for item in payload if isinstance(item, dict)]
        if len(valid) != len(payload):
            log.error(f"[env] Chat history dropped {len(payload) - len(valid)} invalid item(s).")
        log.info(f"[env] Chat history loaded with {len(valid)} items.")
        return cast(list[ChatCompletionMessageParam], valid)


# ==================== 接口 ====================
def save_chat_log() -> Path:
    """把当前对话记录原子写盘（退出时由 clean_up 调用）。"""
    return chat_log.save()


def take_pending() -> list[ChatCompletionContentPartParam]:
    """取走积压的事件消息（取完即清空）。"""
    taken = pending_event_msgs.copy()
    pending_event_msgs.clear()
    return taken


_T = TypeVar("_T")
def run_napcat_async(coro: Coroutine[Any, Any, _T], timeout: float = 30.0) -> _T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("[env] run_on_main_loop 不能在事件循环线程里调用（会等自己）")
    return asyncio.run_coroutine_threadsafe(coro, _main_loop).result(timeout)


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
    global _main_loop

    _main_loop = asyncio.get_running_loop()

    def now_clock_hour() -> float:
        """当前时刻的小数小时（0~24），精确到分钟。"""
        now = datetime.now()
        return now.hour + now.minute / 60.0

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
        if biosim_engine.load_error:
            log.error(f"[env] BioSim 存档未能载入，本次当新的一天：{biosim_engine.load_error}")
        chat_log = ChatLog()
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