from openai import OpenAI
from openai.types.chat import * # pyright: ignore[reportWildcardImportFromLibrary]
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]

from config import config # customized configuration
import modules.core.env as env # global status and variant
from modules.core.logger import log


def get_compressed_context(context: list[ChatCompletionMessageParam]) -> str:
    model_config = config.get_multimodal_env()
    model_name, base_url, apikey = model_config.model_name, model_config.base_url, model_config.apikey
    ai = OpenAI(api_key=apikey, base_url=base_url)
    log.info("Context Compressing Working...")
    sys_prompt = f'''
你是一个严格的上下文压缩器. 请将用户给出的对话压缩成一段连贯的叙事摘要.
[压缩规则]:
1. 保留具体数据, 不得模糊化, 必须舍弃时应当保留上下文使得可以重新从工具获取.
2. 按时间顺序叙述, 清晰记录每件事的结果, 越靠后的事件越重要, 靠前的事件可以适当舍弃细节.
3. 压缩后的文本要能让一个完全不知情的AI, 仅凭这段文字就能理解当前局面.
4. 重复信息只应保留一份.
5. **非常重要** 请使用最客观的语言描述事实，并采用第三人称视角叙述, 不应当使用 "我" "你" 来叙述.
6. **非常重要** 只输出压缩后的文本, 不要任何额外说明.
7. 当前AI助手扮演的角色即 `{env.self_name}`.
8. **非常重要** 总字数不要超过 5000 字, 必要时可从最早的事件开始舍弃.
9. **非常重要** 除了完整的结果以外 *不要* 有任何其他内容.
'''
    messages: list[ChatCompletionMessageParam] = [{'role': 'system', 'content': sys_prompt}]
    messages.append({'role': 'user', 'content': str(context)})
    response = ai.chat.completions.create(
        model=model_name,
        messages=messages,
        stream=False,
        temperature=config.temperature,
    ) # pyright: ignore[reportCallIssue]
    assert response.choices[0].message.content
    log.debug("Compressed context:\n"+response.choices[0].message.content)
    log.info("Compressing finished.")
    return response.choices[0].message.content


