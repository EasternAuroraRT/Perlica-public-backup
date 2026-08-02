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
            log.info("PokeEvent")
            match event:
                case np.FriendPokeEvent():
                    if event.sender_id != env.self_id:
                        prompt = f"User {event.sender_id} poked you."
                case np.GroupPokeEvent():
                    prompt = f'User {event.target_id} was poked in group {event.group_id}.'
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
            env.chatwindows[ChatWindow.ChatType.Group][str(event.group_id)] = ChatWindow(ChatWindow.ChatType.Group, str(event.group_id), event.name_new)
            api_call_msg.append({'type': 'text', 'text':f"You are added to group `{event.name_new}`(id={event.group_id})."})
        case np.GroupIncreaseEvent(): # pyright: ignore[reportGeneralTypeIssues]
            log.info("GroupIncreaseEvent")
            prompt = f'User {event.user_id} is now in group {event.group_id}. You may welcome him/her. Use tool to get more info.'
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
        