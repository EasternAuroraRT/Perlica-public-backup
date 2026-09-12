from __future__ import annotations
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
import logging as log
from pathlib import Path
import threading
from datetime import datetime
import asyncio
import json
from enum import * # pyright: ignore[reportWildcardImportFromLibrary]

import napcat as np
from config import config
from .chatwindow import ChatWindow, chat_type_str
from .logger import log
from ..simulation import biosim as bio

np_ws_url = config.np_ws_url
np_token = config.np_token
settings_folder = Path(__file__).parent.parent / "settings"

npclient = np.NapCatClient(
    ws_url=np_ws_url,
    token=np_token,
)

self_id: int
self_name: str

chatwindows: dict[ChatWindow.ChatType, dict[str, ChatWindow]] = {}
active_chatwindow: ChatWindow

sysprompt: list[ChatCompletionMessageParam] = [] # Do not easily read or write this variant -- unless you know what you are doing!

no_disturb_mode = False

username_list: dict[int, str] = {}

biosim_engine: bio.BioSimEngine = bio.BioSimEngine()

async def init():
    async with npclient:
        global self_id, self_name, chatwindows, active_chatwindow
    # Environment
        chatwindows = {
            ChatWindow.ChatType.Private:{},
            ChatWindow.ChatType.Group:{},
            ChatWindow.ChatType.Unknown:{'void': ChatWindow(ChatWindow.ChatType.Unknown, 'void', 'void')}
        }
        active_chatwindow = chatwindows[ChatWindow.ChatType.Unknown]['void']
        role_prompt: str = ''
        with open(str(settings_folder/"settings.json"), "r", encoding="utf-8") as setting_files:
            settings = json.load(setting_files)
            assert(isinstance(settings, list))
            for setting in settings:
                assert(isinstance(setting, dict))
                for name, path in setting.items():
                    role_prompt += f"\n{name}\n" + open(settings_folder/path, 'r', encoding='utf-8').read() + '\n'
    # About self
        self_info = await npclient.get_login_info()
        self_id = self_info.get("user_id")
        self_name = self_info.get("nickname")
        log.info(f"Self: id={self_id}, nickname={self_name}")
    # Initializing private chats
        friend_list = await npclient.get_friend_list()
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
    # Initializing group chats
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
    # Initializing conversation
        role_prompt += "\nCurrently not in any chat window.\n"
        sysprompt.append({"role": "system", "content": role_prompt})
        if biosim_engine is not None:
            biosim_engine.start()

def reload():
    chatwindows.clear()
    sysprompt.clear()
    asyncio.run(init())

def get_status_prompt() -> str:
    window = active_chatwindow
    return f'''
当前激活聊天窗口：
{chat_type_str[window.chat_type]}`{window.name}`({window.chat_id})
'''

def is_active_time() -> bool:
    # return True
    # [TODO]
    now = datetime.now()
    start = now.replace(hour=6, minute=0, second=0, microsecond=0)
    end   = now.replace(hour=23, minute=0, second=0, microsecond=0)
    return start<=now<=end
