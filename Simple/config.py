import json
import os
from typing import Any, Dict, Tuple
from pathlib import Path

from modules.logger import log

_CONFIG_FILE = Path(__file__).parent/"config.json"
_config: Dict[str, Any] = {}
_mtime: float = 0.0

# ---------- 类型注解（供 Pylance / IDE 识别） ----------
# 这些注解仅用于静态分析，运行时实际值由 __getattr__ 动态提供
_using_model: str
_using_mutimodal: str
_model_config: Dict[str, Any]

model_name: str
base_url: str
apikey: str

temperature: float

np_ws_url: str
np_token: str

weather_host: str
weather_private_key_pem: bytes
weather_project_id: str
weather_credential_id: str

message_compress_lenth_threshold: int
message_debug_max_length: int

# -----------------------------------------------------


def _load_config() -> None:
    """加载外部 JSON 配置文件并更新缓存"""
    global _config, _mtime
    with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
        _config = json.load(f)
    _mtime = os.path.getmtime(_CONFIG_FILE)


def _get_config() -> Dict[str, Any]:
    """获取当前配置，若文件已修改则自动重新加载"""
    global _mtime, _config
    if not _config:
        _load_config()
    else:
        last_modified = os.path.getmtime(_CONFIG_FILE)
        if last_modified != _mtime:
            log.debug("[config->_get_config] Config changed, loading now.")
            _load_config()
    return _config


def __getattr__(name: str) -> Any:
    """
    拦截未定义的属性访问，从配置中动态读取。
    优先采用特殊映射（仅针对历史命名），其余直接以 name 作为键从配置中获取。
    若配置中缺失该键，则抛出 AttributeError。
    """
    config = _get_config()

    if name in ['model_name', 'apikey', 'base_url']:
        try:
            key = config['using_model']
            assert isinstance(key, str)
            value = config['model_config'][key][name]
            log.debug(f"[config.py] {name} = {value}")
            return value
        except Exception as e:
            log.error(f"[config->__getattr__] {e}")
            raise e

    # 特殊名称映射（仅保留历史遗留的两个变量）
    special_map = {
        "_using_model": "using_model",
        "_using_mutimodal": "using_multimodal",
    }
    key = special_map.get(name, name)

    try:
        value = config[key]
    except KeyError:
        raise AttributeError(f"Required configuration key '{key}' not found in {_CONFIG_FILE}")

    # 对 weather_private_key_pem 特殊处理为 bytes
    if name == "weather_private_key_pem":
        if not isinstance(value, str):
            raise AttributeError("weather_private_key_pem must be a string")
        return value.encode('utf-8')

    return value


def is_multimodal() -> bool:
    """判断当前使用的模型是否支持多模态"""
    config = _get_config()
    try:
        using = config["using_model"]
    except KeyError:
        raise RuntimeError("Missing required key 'using_model' in config")
    model_cfg = config.get("model_config")
    if not isinstance(model_cfg, dict):
        raise RuntimeError("'model_config' must be a dict")
    if using not in model_cfg:
        raise RuntimeError(f"Model '{using}' not found in model_config")
    multimodal = model_cfg[using].get("multimodal")
    if not isinstance(multimodal, bool):
        raise RuntimeError(f"Missing or invalid 'multimodal' for model '{using}'")
    return multimodal


def get_multimodal_env() -> Tuple[str, str, str]:
    """获取多模态模型的环境信息（model_name, base_url, apikey）"""
    config = _get_config()
    try:
        using_multi = config["using_multimodal"]
    except KeyError:
        raise RuntimeError("Missing required key 'using_multimodal' in config")
    model_cfg = config.get("model_config")
    if not isinstance(model_cfg, dict):
        raise RuntimeError("'model_config' must be a dict")
    if using_multi not in model_cfg:
        raise RuntimeError(f"Model '{using_multi}' not found in model_config")
    entry = model_cfg[using_multi]
    required = ("model_name", "base_url", "apikey")
    for k in required:
        if k not in entry:
            raise RuntimeError(f"Missing '{k}' for model '{using_multi}'")
    return entry["model_name"], entry["base_url"], entry["apikey"]


# 可选：手动强制重新加载（调试用）
def reload_config() -> None:
    _load_config()