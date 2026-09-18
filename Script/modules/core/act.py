from __future__ import annotations
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from openai import OpenAI
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
import queue
import threading
import time

from config import config # customized configuration
import modules.tools as tools
import modules.core.env as env # global status and variant
from modules.core.logger import log
from modules.core.context_compress import get_compressed_context


MessageContent = list[ChatCompletionContentPartParam]


class _ToolFunctionCall(TypedDict):
    name: str
    arguments: str


class _ToolCall(TypedDict):
    id: str
    type: str
    function: _ToolFunctionCall


class _ChatThread:
    """聊天线程：一个消息缓冲区 + 一个消费线程。只在本文件用。

    - `msg_buf_queue`：还没处理的消息（一次整批取走）；
    - `compressing_messages` + `uncompressed_messages`：对话上下文（旧摘要 + 新消息），
      处理完写回 `env.chat_log`；
    - `processing`：是否正在处理循环里——进循环为真，退出循环立即为假；
    - 缓冲区空了线程就结束，下次 `act()` 再拉起来。
    """

    MAX_CONNECTION_RETRIES = 30

    def __init__(self) -> None:
        self.msg_buf_queue: queue.Queue[MessageContent] = queue.Queue()
        self.compressing_messages: list[ChatCompletionMessageParam] = []
        self.uncompressed_messages: list[ChatCompletionMessageParam] = list(env.chat_log.content())
        # 压缩跑在守护线程上：进程退出时不会 join 它（executor 的非守护 worker 会把退出卡住）
        self.compress_running = False
        self.compress_result: str | None = None
        self.processing = False
        self._lock = threading.Lock()
        self._ai = OpenAI(api_key=config.apikey, base_url=config.base_url)

    def start(self) -> None:
        with self._lock:
            if self.processing:
                return
            self.processing = True
            threading.Thread(target=self.run, name="chat", daemon=True).start()

    def compress(self, messages: list[ChatCompletionMessageParam]) -> None:
        """后台压缩（守护线程）：结果放进 compress_result；失败就算了（历史已经在 compressing 里）。"""
        try:
            self.compress_result = get_compressed_context(messages)
        except Exception as e:
            log.error(f"[chat->compress] {e}")
        finally:
            self.compress_running = False

    def run(self) -> None:
        while self.msg_buf_queue.qsize() > 0:
            if _stop.is_set():
                break
            prompts: MessageContent = []
            while self.msg_buf_queue.qsize() > 0:
                prompts.extend(self.msg_buf_queue.get_nowait())

            if not self.compress_running and self.compress_result is not None:
                self.compressing_messages = [{"role": "user", "content": self.compress_result}]
                self.compress_result = None
            self.uncompressed_messages.append({"role": "user", "content": prompts})

            compressing_befor_tool_backup = self.compressing_messages.copy()
            uncompressed_before_tool_backup = self.uncompressed_messages.copy()
            try:
                should_end = False
                while not should_end:
                    reply = ""
                    collector: dict[int, _ToolCall] = {}
                    fail_times = 0
                    while True:
                        try:
                            stream = self._ai.chat.completions.create(
                                model=config.model_name,
                                messages=env.sysprompt + self.compressing_messages + self.uncompressed_messages,
                                tools=tools.tools_json,
                                tool_choice="auto",
                                stream=True,
                                temperature=config.temperature,
                                extra_body={"thinking": {"type": "disabled"}}
                            )
                            break
                        except ConnectionError as e:
                            fail_times += 1
                            if fail_times > self.MAX_CONNECTION_RETRIES:
                                raise RuntimeError(f"[{__file__}] Connection error occurred for too many times ({fail_times}). Aborted.")
                            log.error(f"[{__file__}] {e} occured when creating api connection. Retrying in 1 second(s)...")
                            time.sleep(1)
                    stopped = False
                    for chunk in stream:
                        if _stop.is_set():      # 进程要退出：立刻停止生成
                            stopped = True
                            break
                        choice = chunk.choices[0]
                        delta = choice.delta
                        if delta.content:
                            reply += delta.content
                            print(delta.content, end="", flush=True)
                        if delta.tool_calls:
                            for tool_call_delta in delta.tool_calls:
                                call = collector.setdefault(tool_call_delta.index, _ToolCall(
                                    id="", type="function",
                                    function=_ToolFunctionCall(name="", arguments=""),
                                ))
                                if tool_call_delta.id:
                                    call["id"] = tool_call_delta.id
                                if tool_call_delta.function:
                                    if tool_call_delta.function.name:
                                        call["function"]["name"] = tool_call_delta.function.name
                                    if tool_call_delta.function.arguments:
                                        call["function"]["arguments"] += tool_call_delta.function.arguments
                        if choice.finish_reason:
                            print()
                            log.debug(f"A round finished with reason {choice.finish_reason}\nLength of reply: {len(reply)}")
                    if stopped:                 # 放弃这次 action（进程在退出）
                        break
                    tool_calls = [collector[index] for index in sorted(collector.keys())]
                    result_message: ChatCompletionMessageParam = {"role": "assistant", "content": reply}
                    tool_results: list[ChatCompletionMessageParam] = []
                    if tool_calls:
                        result_message["tool_calls"] = cast(list[ChatCompletionMessageToolCallUnionParam], tool_calls)
                    else:
                        tool_results.append(cast(ChatCompletionMessageParam, {
                            "role": "user",
                            "content": "You have not called any tool. At least you should call end_action.",
                        }))
                    should_end = False
                    for tool_call in tool_calls:
                        name = tool_call["function"]["name"]
                        arguments = tool_call["function"]["arguments"]
                        log.info(f"[Tool Call] {name}\n[Arguments] {arguments}")
                        if name == "end_action":
                            should_end = True
                            result: List[ChatCompletionContentPartParam] = [{"type": "text", "text": "Action ended."}]
                        else:
                            try:
                                result = tools.call_tool(name, arguments)
                            except Exception as e:
                                log.error(f"[chat->call_tool] {e}")
                                result = [{"type": "text", "text": str(e)}]
                        result_len = len(str(result))
                        log.debug("[Tool Call Result]\n" + str(result)[:config.message_debug_max_length]
                                  + f"{f'... (len={result_len})' if result_len > config.message_debug_max_length else ''}")
                        tool_results.append(cast(ChatCompletionMessageParam, {
                            "role": "tool",
                            "content": result,
                            "tool_call_id": tool_call["id"],
                        }))
                    self.uncompressed_messages.append(result_message)
                    self.uncompressed_messages.extend(tool_results)
                    if self.msg_buf_queue.qsize() > 0:
                        log.debug("Received new msg during tool call.")
                        should_end = False
                        new_prompts: MessageContent = [cast(ChatCompletionContentPartParam, {"type": "text", "text": "New msg rcvd just now:\n"})]
                        while self.msg_buf_queue.qsize() > 0:
                            new_prompts.extend(self.msg_buf_queue.get_nowait())
                        self.uncompressed_messages.append(ChatCompletionUserMessageParam(content=new_prompts, role="user"))
                length = len(str(self.uncompressed_messages))
                log.debug("Uncompressed Messages:\n" + str(self.uncompressed_messages)[:config.message_debug_max_length]
                          + f"{f'... (len={length})' if length > config.message_debug_max_length else ''}")
            except Exception as e:
                log.error(f"[{__file__}->_ChatThread->run] {e}")
                self.compressing_messages = compressing_befor_tool_backup
                self.uncompressed_messages = uncompressed_before_tool_backup
            log.debug(f"Uncompressed len = {len(self.uncompressed_messages)}")
            if len(self.uncompressed_messages) > config.message_compress_lenth_threshold and not self.compress_running:
                log.info("Too many messages. Trying to compress.")
                self.compressing_messages.extend(self.uncompressed_messages)
                snapshot = list(self.compressing_messages)
                self.uncompressed_messages = []
                self.compress_running = True
                threading.Thread(target=self.compress, args=(snapshot,),
                                 name="chat-compress", daemon=True).start()
            env.chat_log.replace(self.compressing_messages + self.uncompressed_messages)
        self.processing = False
        log.info("Current chat finished")


_chat: _ChatThread | None = None
_stop = threading.Event()


def stop() -> None:
    """请求中止正在进行的生成（进程要退出时调用；新消息也不再有意义）。"""
    _stop.set()


def act(prompt: MessageContent, high_priority: bool = False) -> None:
    global _chat
    if _chat is None:
        # Initialize on first call
        _chat = _ChatThread()
    log.debug("Last prompt:\n" + str(prompt))
    if high_priority:
        log.debug("Currently high priority task.")
    _chat.msg_buf_queue.put(prompt)
    if not _chat.processing:
        _chat.start()
