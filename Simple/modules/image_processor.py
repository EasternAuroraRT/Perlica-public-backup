import requests
import base64
from openai import OpenAI

from modules.logger import log
import config

def get_image_base64_from_url(url: str, as_data_uri: bool = True) -> str:
    log.debug("Getting base64...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # 若状态码不是 2xx 则抛出异常
        image_data = response.content
        # 推断 MIME 类型（可从 Content-Type 或文件头判断）
        content_type = response.headers.get('content-type', 'application/octet-stream')
        # 如果 Content-Type 不明确，可根据文件头魔数识别（这里简单处理）
        if content_type == 'application/octet-stream':
            # 根据常见图片格式补充，也可省略（AI API 通常接受 data:image/*;base64）
            if image_data.startswith(b'\xff\xd8'):       # JPEG
                content_type = 'image/jpeg'
            elif image_data.startswith(b'\x89PNG'):      # PNG
                content_type = 'image/png'
            elif image_data.startswith(b'GIF'):          # GIF
                content_type = 'image/gif'
            # 其余可按需添加
        # Base64 编码
        base64_str = base64.b64encode(image_data).decode('utf-8')
        if as_data_uri:
            return f"data:{content_type};base64,{base64_str}"
        return base64_str
    except Exception as e:
        log.error(f"[image_processor->get_image_base64_from_url] {e}")
        raise e

def get_image_base64_from_path(path: str, as_data_uri: bool = True) -> str:
    """
    从本地文件路径读取图片并返回 Base64 编码，可选择 data URI 格式。
    """
    log.debug(f"Reading image from path: {path}")
    try:
        with open(path, 'rb') as f:
            image_data = f.read()
    except Exception as e:
        log.error(f"[image_processor->get_image_base64_from_path] Failed to read file: {e}")
        raise e

    # 推断 MIME 类型（基于文件头魔数）
    content_type = 'application/octet-stream'
    if image_data.startswith(b'\xff\xd8'):       # JPEG
        content_type = 'image/jpeg'
    elif image_data.startswith(b'\x89PNG'):      # PNG
        content_type = 'image/png'
    elif image_data.startswith(b'GIF'):          # GIF
        content_type = 'image/gif'
    # 可根据需要添加更多类型

    base64_str = base64.b64encode(image_data).decode('utf-8')
    if as_data_uri:
        return f"data:{content_type};base64,{base64_str}"
    return base64_str

def get_image_description_from_base64(image64: str, user_prompt: str|None = None) -> str:
    msgs = [
            {'role': 'system', 'content': '''
你需要详细地描述给出来的图片。描述时你应当遵守以下规则：
1. 尽可能详细地描述图片。
2. 请保持绝对客观，不要对图中内容是什么加以额外判断。
3. 加以关注图片中各元素的空间关系；如果图片内容有排版，请描述出排版层级。
4. 请注意从整体到局部的描述顺序；不要因为细节丢弃整体关系；整体和局部是相对的，可能某些局部也构成小的整体。
5. 如果你确实认识其中的人物、事物，可以直接说名字，但不得在“疑似”或“可能”的时候这样做。
'''},
            {'role': 'user', 'content': [{'type': 'image_url', 'image_url':{'url': image64}}]}
        ]
    if user_prompt:
        msgs.append({'role': 'user', 'content': [{'type': 'text', 'text': user_prompt}]})
    model_config = config.get_multimodal_env()
    model_name, base_url, apikey = model_config.model_name, model_config.base_url, model_config.apikey
    log.debug(f"[get_image_base64_from_url] Adding multimodal chat: model={model_name}, url={base_url}")
    ai = OpenAI(api_key=apikey, base_url=base_url)
    chat = ai.chat.completions.create(
        model=model_name,
        messages=msgs,
    )
    result = chat.choices[0].message.content
    assert result, str
    log.debug(f"Image description:\n{result}")
    return result

def get_image_description_from_url(url: str, user_prompt: str|None = None) -> str:
    try:
        image64 = get_image_base64_from_url(url)
    except Exception as e:
        log.error(f"[image_processor->get_image_base64_from_url] Failed to describe image from url.")
        raise e
    return get_image_description_from_base64(image64, user_prompt)