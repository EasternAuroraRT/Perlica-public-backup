from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
import threading
from openai import OpenAI
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
from concurrent.futures import ThreadPoolExecutor, Future

import config # customized configuration
import modules.tools as tools
import modules.env as env # global status and variant
from modules.logger import log
from modules.context_compress import get_compressed_context

# region Initialization

def chat(last_prompt: str|list, high_priority: bool = False) -> None:
    log.debug("Prompt:\n"+str(last_prompt))
    if high_priority:
        log.debug("Currently high priority task.")
    threading.Thread(target=_call_api, args=(last_prompt, high_priority,)).start()

# -------- Chat Lock --------
_cond = threading.Condition()
_current_worker: Optional[int] = None
_stop_event = threading.Event()
# ---------------------------

_uncompressed_messages: list[ChatCompletionMessageParam] = []
_compressing_messages: list[ChatCompletionMessageParam] = []
_compress_thread: Optional[Future] = None

def _call_api(last_prompt: str|list, high_priority: bool) -> None:
    ai = OpenAI(api_key=config.apikey, base_url=config.base_url)
    global _current_worker, _uncompressed_messages, _compressing_messages, _compress_thread
    # Waiting
    my_id = threading.get_ident()
    with _cond:
        interrupt_sent = False
        while _current_worker is not None:
            log.debug("Waiting for lock...")
            if not interrupt_sent:
                _stop_event.set()
                interrupt_sent = True
            _cond.wait()
        # 现在无工作线程，获得工作权
        _current_worker = my_id
        _stop_event.clear()
    # Initializing
    log.info("Trying to add a new chat thread...")
    messages: list[ChatCompletionMessageParam] = env.sysprompt.copy()
    compressing_bkup = _compressing_messages.copy()
    uncompressed_bkup = _uncompressed_messages.copy()
    if _compress_thread and _compress_thread.done():
        try:
            compress_result = _compress_thread.result()
            log.debug("New compressed results received.")
            _compressing_messages = [{'role': 'user', 'content': compress_result}]
        except Exception as e:
            log.error(f"[chat->_call_api->_compress_thread] {e}")
        _compress_thread = None
    _uncompressed_messages.append({"role": "user", "content": last_prompt})
    log.debug(f"msg round len  = {len(messages)}\nmsg string len = {len(str(messages))}")
    # Working
    should_end: bool = False
    try:
        while not should_end:
            reply: str = ''
            stream = ai.chat.completions.create(
                model=config.model_name,
                messages=messages+_compressing_messages+_uncompressed_messages,
                tools=tools.tools_json,
                tool_choice="auto",
                stream=True,
                temperature=config.temperature,
                extra_body={"thinking": {"type": "disabled"}}
            ) # pyright: ignore[reportCallIssue]
            tool_calls_collector = {}
            for chunk in stream: # pyright: ignore[reportGeneralTypeIssues]
                choice = chunk.choices[0]
                delta = choice.delta
                if not high_priority:
                    if _stop_event.is_set():
                        reply += "\n<Interrupted by New Event>\n"
                        log.info("Interrupted by new event.")
                        should_end = True
                        break
                if delta.content:
                    reply += delta.content
                    print(delta.content, end="", flush=True)
                if delta.tool_calls:
                    for tool_call_delta in delta.tool_calls:
                        if tool_call_delta.index not in tool_calls_collector:
                            tool_calls_collector[tool_call_delta.index] = {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if tool_call_delta.id:
                            tool_calls_collector[tool_call_delta.index]["id"] = tool_call_delta.id
                        if tool_call_delta.function:
                            if tool_call_delta.function.name:
                                tool_calls_collector[tool_call_delta.index]["function"]["name"] = tool_call_delta.function.name
                            if tool_call_delta.function.arguments:
                                tool_calls_collector[tool_call_delta.index]["function"]["arguments"] += tool_call_delta.function.arguments
                if choice.finish_reason:
                    print()
                    log.debug(f"A round finished with reason {choice.finish_reason}\nLength of reply: {len(reply)}")
            tool_calls_list = [tool_calls_collector[i] for i in tool_calls_collector.keys()]
            result_message: ChatCompletionMessageParam = {'role': 'assistant', 'content': reply}
            if should_end:
                _uncompressed_messages.append(result_message) # Without calling tools we add clean reply in case api call failure.
                break
            if tool_calls_list:
                result_message['tool_calls'] = tool_calls_list
                should_end = False
            else: should_end = True
            tool_call_results: list = []
            for tool_call_delta in tool_calls_list:
                log.info(f"[Tool Call] {tool_call_delta['function']['name']}\n[Arguments] {tool_call_delta['function']['arguments']}")
                tool_call_result: list
                if tool_call_delta['function']['name'] == 'end_reply':
                    should_end = True
                    tool_call_result = [{'type': 'text', 'text': "Reply ended."}]
                else:
                    try:
                        tool_call_result = tools.call_tool(tool_call_delta['function']['name'], tool_call_delta['function']['arguments'])
                    except Exception as e:
                        log.error(f"[chat->_call_api->call_tool] {e}")
                        tool_call_result = [{'type': 'text', 'text': str(e)}]
                _tool_cal_result_len = len(str(tool_call_result))
                log.debug(f"[Tool Call Result]\n"+str(tool_call_result)[:config.message_debug_max_length]+f"{f'... (len={_tool_cal_result_len})'if _tool_cal_result_len>config.message_debug_max_length else ''}")
                tool_call_results.append({'role': 'tool', 'content': tool_call_result, "tool_call_id": tool_call_delta['id']})
            _uncompressed_messages.append(result_message)
            _uncompressed_messages.extend(tool_call_results)
        _uncompressed_str_len = len(str(_uncompressed_messages))
        log.debug("Uncompressed Messages:\n"+str(_uncompressed_messages)[:config.message_debug_max_length]+f"{f'... (len={_uncompressed_str_len})'if _uncompressed_str_len>config.message_debug_max_length else ''}")
        if len(_uncompressed_messages) > config.message_compress_lenth_threshold:
            log.info("Too many messages. Trying to compress.")
            if not _compress_thread:
                _compressing_messages.extend(_uncompressed_messages)
                _compress_thread = ThreadPoolExecutor().submit(get_compressed_context, _compressing_messages)
                _uncompressed_messages.clear()

    except Exception as e:
        log.error(f"[chat->_call_api->chat failure] {e}")
        _compressing_messages = compressing_bkup
        _uncompressed_messages = uncompressed_bkup
    # Cleaning
    finally:
        log.info("Current chat finished")
        with _cond:
            print()
            if _current_worker == my_id:
                _current_worker = None
                _cond.notify_all()