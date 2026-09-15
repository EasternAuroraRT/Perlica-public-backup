from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
import threading
from openai import OpenAI
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
from concurrent.futures import ThreadPoolExecutor, Future
import time

from config import config # customized configuration
import modules.tools as tools
import modules.core.env as env # global status and variant
from modules.core.logger import log
from modules.core.context_compress import get_compressed_context

class ChatThreadData:
    condition: threading.Condition = threading.Condition()
    stop_event: threading.Event = threading.Event()
    current_worker: Optional[int] = None
    compress_thread: Optional[Future] = None
    compressing_messages: list[ChatCompletionMessageParam] = []
    uncompressed_messages: list[ChatCompletionMessageParam] = []

    def __init__(self) -> None:
        self.uncompressed_messages = env.chat_log

chat_data1: ChatThreadData = ChatThreadData()


def act(last_prompt: str | list[ChatCompletionContentPartParam], high_priority: bool = False) -> None:
    log.debug("Last prompt:\n"+str(last_prompt))
    if high_priority:
        log.debug("Currently high priority task.")
    log.info("Trying to add a new chat thread...")
    threading.Thread(target=_chat_thread_func, args=(chat_data1, last_prompt, high_priority,), daemon=True).start()


def _acquire_worker(chat_thread_data: ChatThreadData) -> int:
    my_id = threading.get_ident()
    with chat_thread_data.condition:
        interrupt_sent = False
        while chat_thread_data.current_worker is not None:
            log.debug("Waiting for lock...")
            if not interrupt_sent:
                chat_thread_data.stop_event.set()
                interrupt_sent = True
            chat_thread_data.condition.wait()
        # 现在无工作线程，获得工作权
        chat_thread_data.current_worker = my_id
        chat_thread_data.stop_event.clear()
    return my_id


def _release_worker(chat_thread_data: ChatThreadData, my_id: int) -> None:
    global _current_worker
    with chat_thread_data.condition:
        print()
        if chat_thread_data.current_worker == my_id:
            chat_thread_data.current_worker = None
            chat_thread_data.condition.notify_all()


def _compress_message(chat_thread_data: ChatThreadData) -> None:
    if not chat_thread_data.compress_thread:
        chat_thread_data.compressing_messages.extend(chat_thread_data.uncompressed_messages)
        chat_thread_data.compress_thread = ThreadPoolExecutor().submit(get_compressed_context, chat_thread_data.compressing_messages)
        chat_thread_data.uncompressed_messages.clear()


def _chat_thread_func(cur_chat_data: ChatThreadData, last_prompt: str|List[ChatCompletionContentPartParam], high_priority: bool) -> None:
    ai = OpenAI(api_key=config.apikey, base_url=config.base_url)
    my_id = _acquire_worker(cur_chat_data)
    # Initializing
    compressing_messages = cur_chat_data.compressing_messages
    uncompressed_messages = cur_chat_data.uncompressed_messages
    compressing_bkup = compressing_messages.copy()
    uncompressed_bkup = uncompressed_messages.copy()
    # Check compressed message
    if cur_chat_data.compress_thread and cur_chat_data.compress_thread.done():
        try:
            compress_result = cur_chat_data.compress_thread.result()
            log.debug("New compressed results received.")
            cur_chat_data.compressing_messages = [{'role': 'user', 'content': compress_result}]
        except ConnectionError as e:
            log.error(f"Retrying for ConnectionError: {e}")
        except Exception as e:
            log.error(f"[chat->_apply_compress_result] Chat loop ended: {e}")
            raise RuntimeError("Chat loop meets fatal error.")
        cur_chat_data.compress_thread = None
    uncompressed_messages.append({"role": "user", "content": last_prompt})
    # Working
    should_end: bool = False
    try:
        while not should_end:
            # Send request
            reply: str = ''
            tool_calls_collector: dict[int, dict] = {}
            interrupted = False
            while True:
                fail_times = 0
                try:
                    stream = ai.chat.completions.create(
                        model=config.model_name,
                        messages=env.sysprompt + compressing_messages + uncompressed_messages,
                        tools=tools.tools_json,
                        tool_choice="auto",
                        stream=True,
                        temperature=config.temperature,
                        extra_body={"thinking": {"type": "disabled"}}
                    )
                    break
                except ConnectionError as e:
                    fail_times += 1
                    if fail_times > 30:
                        raise RuntimeError(f"{__file__}] Connection error occurred for too many times ({fail_times}). Aborted.")
                    sleeptime = 1
                    log.error(f"[{__file__}] {e} occured when creating api connection. Retrying in {sleeptime} second(s)...")
                    time.sleep(sleeptime)
            # Dealing result
            for chunk in stream:
                choice = chunk.choices[0]
                delta = choice.delta
                if not high_priority:
                    if cur_chat_data.stop_event.is_set():
                        reply += "\n<Interrupted by New Event>\n"
                        log.info("Interrupted by new event.")
                        interrupted = True
                        break
                if delta.content:
                    reply += delta.content
                    print(delta.content, end="", flush=True)
                if delta.tool_calls:
                    for tool_call_delta in delta.tool_calls:
                        collector = tool_calls_collector.setdefault(tool_call_delta.index, {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                        if tool_call_delta.id:
                            collector["id"] = tool_call_delta.id
                        if tool_call_delta.function:
                            if tool_call_delta.function.name:
                                collector["function"]["name"] = tool_call_delta.function.name
                            if tool_call_delta.function.arguments:
                                collector["function"]["arguments"] += tool_call_delta.function.arguments
                if choice.finish_reason:
                    print()
                    log.debug(f"A round finished with reason {choice.finish_reason}\nLength of reply: {len(reply)}")
            tool_calls_list = [tool_calls_collector[i] for i in tool_calls_collector.keys()]
            tool_call_results: list[ChatCompletionMessageParam] = []
            result_message: ChatCompletionMessageParam = {'role': 'assistant', 'content': reply}
            if interrupted:
                uncompressed_messages.append(result_message)
                break
            if tool_calls_list:
                result_message['tool_calls'] = cast(list[ChatCompletionMessageToolCallUnionParam], tool_calls_list)
            else:
                tool_call_results.append(cast(ChatCompletionMessageParam, {
                    'role': 'user',
                    'content': 'You have not called any tool. At least you should call end_action.',
                }))
            should_end = False
            # Tool calling
            for tool_call in tool_calls_list:
                name = tool_call['function']['name']
                arguments = tool_call['function']['arguments']
                log.info(f"[Tool Call] {name}\n[Arguments] {arguments}")
                if name == 'end_action':
                    should_end = True
                    tool_call_result: List[ChatCompletionContentPartParam] = [{'type': 'text', 'text': "Action ended."}]
                else:
                    try:
                        tool_call_result = tools.call_tool(name, arguments)
                    except Exception as e:
                        log.error(f"[chat->_execute_tool_calls->call_tool] {e}")
                        tool_call_result = [{'type': 'text', 'text': str(e)}]
                _tool_cal_result_len = len(str(tool_call_result))
                log.debug(f"[Tool Call Result]\n"+str(tool_call_result)[:config.message_debug_max_length]+f"{f'... (len={_tool_cal_result_len})'if _tool_cal_result_len>config.message_debug_max_length else ''}")
                tool_call_results.append(cast(ChatCompletionMessageParam, {
                    'role': 'tool',
                    'content': tool_call_result,
                    "tool_call_id": tool_call['id'],
                }))
            # Post processing
            uncompressed_messages.append(result_message)
            uncompressed_messages.extend(tool_call_results)
        # After a loop
        _uncompressed_str_len = len(str(uncompressed_messages))
        log.debug("Uncompressed Messages:\n"+str(uncompressed_messages)[:config.message_debug_max_length]+f"{f'... (len={_uncompressed_str_len})'if _uncompressed_str_len>config.message_debug_max_length else ''}")
        if len(uncompressed_messages) > config.message_compress_lenth_threshold:
            log.info("Too many messages. Trying to compress.")
            _compress_message(cur_chat_data)
    except Exception as e:
        log.error(f"[act->_chat_thread_func->chat failure] {e}")
        compressing_messages = compressing_bkup
        uncompressed_messages = uncompressed_bkup
    # Cleaning
    finally:
        log.info("Current chat finished")
        env.chat_log = compressing_messages + uncompressed_messages
        _release_worker(cur_chat_data, my_id)
