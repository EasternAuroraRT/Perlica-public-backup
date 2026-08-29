from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from enum import Enum, Flag, auto, unique

import napcat as np
import modules.core.env as env
import modules.core.history as history
from modules.core.logger import log

class ChatWindow(Sized):
    @unique
    class ChatType(Enum):
        def __repr__(self) -> str:
            return self.name
        def __str__(self) -> str:
            return self.name
        Private = auto()
        Group = auto()
        Unknown = auto()

    def __init__(self, type: ChatWindow.ChatType, id: str, name: str):
        self.chat_type = type
        self.chat_id = id
        self.name = name
        self.content: list[np.Message] = []
        self.__cursor = 0

    @property
    def cursor(self) -> int:
        return self.__cursor
    
    @cursor.setter
    def cursor(self, value: int):
        self.__cursor = max(0, min(value, len(self.content)))

    def __len__(self) -> int:
        return len(self.content)
    
    def __str__(self) -> str:
        result: str = ''
        for x in self.content:
            result += str(x)
        return result
    
    def __bool__(self) -> bool:
        return self.chat_type != self.ChatType.Unknown
    
    def __getitem__(self, key):
        return self.content[key]
    
    def type_txt(self, txt: str):
        self.content.insert(self.__cursor, np.Text(text=txt))
        self.__cursor += 1

    def type_del(self, num: int):
        if num <= 0 : return
        cutpos = self.__cursor - num
        if cutpos < 0: cutpos = 0
        self.content = self.content[:cutpos] + self.content[self.__cursor:]
        self.__cursor = cutpos

    def move_cursor(self, num: int):
        self.__cursor = max(0, min(self.__cursor + num, len(self.content)))

    def clear(self):
        self.content.clear()
        self.__cursor = 0

    def get(self) -> list[np.Message]:
        return self.content
    
    def type_qface(self, faceid: str):
        self.content.insert(self.__cursor, np.Face(id=faceid))
        self.__cursor += 1
    
    def add_image(self, image_url):
        self.content.insert(self.__cursor, np.Image(file=image_url))
        self.__cursor += 1

    def get_info(self) -> str:
        return f"type: {self.chat_type}\nid: {self.chat_id}"
    
    def _message_type(self) -> Literal["private", "group"]:
        return "group" if self.chat_type is ChatWindow.ChatType.Group else "private"

    async def send(self) -> bool:
        if not self:
            return False
        log.info(f"Sending {chat_type_str[self.chat_type]} msg to {self.name}({self.chat_id})")
        response = await env.npclient.send_msg(
            message_type=self._message_type(),
            group_id=self.chat_id,
            user_id=self.chat_id,
            message=self.content.copy()
        )
        msg_id = response.get('message_id')
        history.store_message(
            chat_type_str[self.chat_type],
            self.chat_id,
            self.content, # pyright: ignore[reportArgumentType]
            sender_id=env.self_id,
            sender_nickname=env.self_name,
            msg_id=msg_id
        )
        self.clear()
        return True

chat_type_str: dict[ChatWindow.ChatType, Literal["private", "group", "unknown"]] = {
    ChatWindow.ChatType.Private: "private",
    ChatWindow.ChatType.Group: "group",
    ChatWindow.ChatType.Unknown: "unknown",
}

chat_type_str_cn: dict[ChatWindow.ChatType, str] = {
    ChatWindow.ChatType.Private: "私聊",
    ChatWindow.ChatType.Group: "群聊",
}