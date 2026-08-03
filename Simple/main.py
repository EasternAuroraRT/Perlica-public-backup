from __future__ import annotations
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
import asyncio
import time
from datetime import datetime
import threading

import napcat as np
import modules.chat as chat
import modules.env as env
import modules.qmessage as qmsg
import modules.history as history
from modules.chatwindow import ChatWindow
from modules.logger import log

def is_active_time() -> bool:
    # [TODO]
    now = datetime.now()
    start = now.replace(hour=8, minute=0, second=0, microsecond=0)
    end   = now.replace(hour=23, minute=0, second=0, microsecond=0)
    return start<=now<=end

stored_events: list[np.NapCatEvent] = []

async def consume_event(event) -> list[dict]:
    prompt: str = ''
    api_call_msg: list[dict] = []
    async def get_user_nickname(user_id: str|int) -> str:
        return (await env.npclient.get_stranger_info(user_id=str(user_id)))['nickname']
    match event:
        case np.MessageEvent():
            log.info("Msg RCVD")
            log.debug(str(event.message))
            match event:
                case np.PrivateMessageEvent():
                    history.store_private_message(event)
                    prompt = f"Received private message from user `{event.sender.nickname}` (id:{event.sender.user_id}): "
                case np.GroupMessageEvent():
                    history.store_group_message(event)
                    prompt = f"Received group message from user `{event.sender.nickname}` (id:{event.sender.user_id}) in group `{event.group_name}` (id:{event.group_id}): "
            api_call_msg.append({'type':'text', 'text':prompt})
            api_call_msg.extend(qmsg.parse_msg_to_list(event.message))
        case np.PokeEvent(): # pyright: ignore[reportGeneralTypeIssues]
            def parse_poke_raw(data, sender_name: str, target_name: str) -> str:
                parts = []
                uid_counter = 0
                for item in data:
                    if 'uid' in item:
                        if uid_counter == 0:
                            parts.append(str(sender_name))
                        elif uid_counter == 1:
                            parts.append(str(target_name))
                        uid_counter += 1
                    elif 'txt' in item:
                        parts.append(item['txt'])
                return ''.join(parts)
            log.info("PokeEvent")
            sender_nickname: str = await get_user_nickname(event.sender_id)
            match event:
                case np.FriendPokeEvent():
                    if event.sender_id != env.self_id:
                        prompt = f"User `{sender_nickname}` poked you in the chatbox. Interaction msg: {parse_poke_raw(event.raw_info, sender_nickname, '你')}"
                case np.GroupPokeEvent():
                    target_nickname: str = await get_user_nickname(event.target_id)
                    prompt = f'{ 'You were' if event.target_id == env.self_id else f"User `{target_nickname}` was"} poked in group {event.group_id} in the chatbox. Interaction msg: {parse_poke_raw(event.raw_info, sender_nickname, '你' if event.target_id == env.self_id else target_nickname)}'
            api_call_msg.append({'type': 'text', 'text': prompt})
        case np.HeartbeatEvent():
            log.info("Heartbeat RCVD")
            if is_active_time():
                prompt = "Currently Heartbeat Event. You can do anything you want, including writing diary or other files, and actively chatting with someone or in any group. You'd better watch the time first."
                api_call_msg.append({'type':'text', 'text': prompt})
        case np.FriendAddNoticeEvent(): # pyright: ignore[reportGeneralTypeIssues]
            new_friend_name: str = (await npclient.get_stranger_info(user_id=str(event.user_id))).get('nickname', '')
            if isinstance(new_friend_name, str):
                env.chatwindows[ChatWindow.ChatType.Private][str(event.user_id)] = ChatWindow(ChatWindow.ChatType.Private, str(event.user_id), new_friend_name)
                api_call_msg.append({'type': 'text', 'text':f"A new friend (id={event.user_id}, nickname={new_friend_name}) is added."})
        case np.InputStatusEvent(): # pyright: ignore[reportGeneralTypeIssues]
            pass
        case np.GroupNameEvent(): # pyright: ignore[reportGeneralTypeIssues]
            log.info("GroupNameEvent")
            api_call_msg.append({'type': 'text', 'text':f"Group `{event.name_new}`(id={event.group_id}) is now named as {event.name_new}."})
        case np.GroupIncreaseEvent(): # pyright: ignore[reportGeneralTypeIssues]
            log.info("GroupIncreaseEvent")
            if not str(event.group_id) in env.chatwindows[ChatWindow.ChatType.Group]:
                env.chatwindows[ChatWindow.ChatType.Group][str(event.group_id)] = ChatWindow(ChatWindow.ChatType.Group, str(event.group_id), event.name_new)
                prompt = f'You are now added to group {event.group_id}.'
            else:
                prompt = f'User {event.user_id} is added to group {event.group_id}. You may welcome him/her. Use tool to get more info.'
            api_call_msg.append({'type': 'text', 'text': prompt})
        case np.GroupDecreaseEvent(): # pyright: ignore[reportGeneralTypeIssues]
            log.info("GroupDecreaseEvent")
            prompt = f'{"You are" if event.user_id == env.self_id else f"User {event.user_id} is"} removed from group {event.group_id}.'
            if event.sub_type == 'kick_me':
                del env.chatwindows[ChatWindow.ChatType.Group][str(event.group_id)]
            api_call_msg.append({'type': 'text', 'text': prompt})
        case _:
            log.info(f'Event not recognized: {event}')
    return api_call_msg

async def main() -> None:
    await env.init()
    npclient = env.npclient
    async for event in npclient:
        api_call_msg = []
        if env.no_disturb_mode and not isinstance(event, np.HeartbeatEvent):
            stored_events.append(event)
            continue
        api_call_msg.extend(await consume_event(event))
        if api_call_msg:
            chat.chat(api_call_msg)
            

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            log.error(f"[main] {e}")
            time.sleep(1)
        