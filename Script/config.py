from __future__ import annotations
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]

_CONFIG_FILE = Path(__file__).parent / "config.json"

_MODEL_FIELDS = ("model_name", "base_url", "apikey", "multimodal")

_TOP_FIELDS: Dict[str, type | tuple[type, ...]] = {
    "using_model": str,
    "using_multimodal": str,
    "using_compress_model": str,
    "temperature": (int, float),
    "np_ws_url": str,
    "np_token": str,
    "weather_host": str,
    "weather_private_key_pem": str,
    "weather_project_id": str,
    "weather_credential_id": str,
    "message_compress_lenth_threshold": int,
    "message_debug_max_length": int,
}


using_model: str
using_multimodal: str
using_compress_model: str
temperature: float
np_ws_url: str
np_token: str
weather_host: str
weather_private_key_pem: bytes
weather_project_id: str
weather_credential_id: str
message_compress_lenth_threshold: int
message_debug_max_length: int
model_config: Dict[str, ModelConfig]

model_name: str
base_url: str
apikey: str


@dataclass(frozen=True)
class ModelConfig:
    model_name: str
    base_url: str
    apikey: str
    is_multimodal: bool
    can_upload_file: bool


@dataclass(frozen=True)
class Config:
    using_model: str
    using_multimodal: str
    using_compress_model: str
    temperature: float
    np_ws_url: str
    np_token: str
    weather_host: str
    weather_private_key_pem: bytes
    weather_project_id: str
    weather_credential_id: str
    message_compress_lenth_threshold: int
    message_debug_max_length: int
    model_config: Dict[str, ModelConfig]

    @property
    def model_name(self) -> str:
        return getattr(self.model_config[self.using_model], "model_name")

    @property
    def base_url(self) -> str:
        return getattr(self.model_config[self.using_model], "base_url")

    @property
    def apikey(self) -> str:
        return getattr(self.model_config[self.using_model], "apikey")

    def model_env(self, using_name: str) -> ModelConfig:
        return self.model_config[using_name]


def _parse(raw: Dict[str, Any]) -> Config:
    for key, t in _TOP_FIELDS.items():
        if key not in raw:
            raise RuntimeError(f"Missing required config key '{key}' in {_CONFIG_FILE}")
        if not isinstance(raw[key], t):
            raise RuntimeError(f"Config key '{key}' must be {getattr(t, '__name__', t)}, got {type(raw[key]).__name__}")

    raw_models = raw["model_config"]
    if not isinstance(raw_models, dict):
        raise RuntimeError("'model_config' must be a dict")
    models: Dict[str, ModelConfig] = {}
    for name, entry in raw_models.items():
        if not isinstance(entry, dict):
            raise RuntimeError(f"model_config['{name}'] must be a dict")
        missing = set(_MODEL_FIELDS) - set(entry)
        if missing:
            raise RuntimeError(f"model_config['{name}'] missing keys: {sorted(missing)}")
        if not isinstance(entry["multimodal"], bool):
            raise RuntimeError(f"model_config['{name}'].multimodal must be a bool")
        models[name] = ModelConfig(
            model_name=entry.get("model_name", "unknown"),
            base_url=entry.get("base_url", "unknown"),
            apikey=entry.get("apikey", "null"),
            is_multimodal=entry.get("multimodal", False),
            can_upload_file=entry.get("can_upload_file", False)
        )
    if not models:
        raise RuntimeError("'model_config' is empty")

    for key in ("using_model", "using_multimodal", "using_compress_model"):
        if raw[key] not in models:
            raise RuntimeError(f"config key '{key}' = '{raw[key]}' not found in model_config")

    return Config(
        using_model=raw["using_model"],
        using_multimodal=raw["using_multimodal"],
        using_compress_model=raw["using_compress_model"],
        temperature=raw["temperature"],
        np_ws_url=raw["np_ws_url"],
        np_token=raw["np_token"],
        weather_host=raw["weather_host"],
        weather_private_key_pem=raw["weather_private_key_pem"].encode("utf-8"),
        weather_project_id=raw["weather_project_id"],
        weather_credential_id=raw["weather_credential_id"],
        message_compress_lenth_threshold=raw["message_compress_lenth_threshold"],
        message_debug_max_length=raw["message_debug_max_length"],
        model_config=models,
    )


_cfg: Optional[Config] = None
_mtime: float = 0.0


def _get_config() -> Config:
    global _cfg, _mtime
    mtime = os.path.getmtime(_CONFIG_FILE)
    if _cfg is None or mtime != _mtime:
        _cfg = _parse(json.loads(_CONFIG_FILE.read_text(encoding="utf-8")))
        _mtime = mtime
    return _cfg


def __getattr__(name: str) -> Any:
    if name.startswith("__"):
        raise AttributeError(name)
    try:
        return getattr(_get_config(), name)
    except AttributeError:
        raise AttributeError(f"Config has no attribute '{name}' (check {_CONFIG_FILE})") from None


def is_multimodal() -> bool:
    cfg = _get_config()
    return cfg.model_config[cfg.using_model].is_multimodal


def get_multimodal_env() -> ModelConfig:
    cfg = _get_config()
    return cfg.model_env(cfg.using_multimodal)


def get_compress_env() -> ModelConfig:
    cfg = _get_config()
    return cfg.model_env(cfg.using_compress_model)


def reload_config() -> None:
    global _cfg, _mtime
    _cfg = _parse(json.loads(_CONFIG_FILE.read_text(encoding="utf-8")))
    _mtime = os.path.getmtime(_CONFIG_FILE)


_cfg = _get_config()
