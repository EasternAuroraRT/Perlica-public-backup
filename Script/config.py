from __future__ import annotations
import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]

_CONFIG_FILE = Path(__file__).parent / "config.json"

_MODEL_FIELDS = ("model_name", "base_url", "apikey", "multimodal")


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
    manager_id: int
    model_config: Dict[str, ModelConfig]
    enable_rag: bool
    datadir_real: str
    datadir_napcat: str

    @property
    def model_name(self) -> str:
        return self.model_config[self.using_model].model_name

    @property
    def base_url(self) -> str:
        return self.model_config[self.using_model].base_url

    @property
    def apikey(self) -> str:
        return self.model_config[self.using_model].apikey

    def model_env(self, using_name: str) -> ModelConfig:
        return self.model_config[using_name]

    def is_multimodal(self) -> bool:
        return self.model_config[self.using_model].is_multimodal

    def get_multimodal_env(self) -> ModelConfig:
        return self.model_env(self.using_multimodal)

    def get_compress_env(self) -> ModelConfig:
        return self.model_env(self.using_compress_model)


def _coerce(name: str, value: Any, field_type: type) -> Any:
    if field_type is bytes:
        if not isinstance(value, str):
            raise RuntimeError(
                f"Config key '{name}' must be a str (encoded to bytes), got {type(value).__name__}"
            )
        return value.encode("utf-8")
    if isinstance(value, bool) and field_type is not bool:
        raise RuntimeError(
            f"Config key '{name}' must be {field_type.__name__}, got bool"
        )
    if field_type is float:
        if isinstance(value, (int, float)):
            return float(value)
        raise RuntimeError(
            f"Config key '{name}' must be {field_type.__name__}, got {type(value).__name__}"
        )
    if not isinstance(value, field_type):
        raise RuntimeError(
            f"Config key '{name}' must be {field_type.__name__}, got {type(value).__name__}"
        )
    return value


def _parse_models(raw: Any) -> Dict[str, ModelConfig]:
    if not isinstance(raw, dict):
        raise RuntimeError("'model_config' must be a dict")
    models: Dict[str, ModelConfig] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise RuntimeError(f"model_config['{name}'] must be a dict")
        missing = set(_MODEL_FIELDS) - set(entry)
        if missing:
            raise RuntimeError(f"model_config['{name}'] missing keys: {sorted(missing)}")
        if not isinstance(entry["multimodal"], bool):
            raise RuntimeError(f"model_config['{name}'].multimodal must be a bool")
        models[name] = ModelConfig(
            model_name=entry["model_name"],
            base_url=entry["base_url"],
            apikey=entry["apikey"],
            is_multimodal=entry["multimodal"],
            can_upload_file=entry.get("can_upload_file", False),
        )
    if not models:
        raise RuntimeError("'model_config' is empty")
    return models


def _parse(raw: Dict[str, Any]) -> Config:
    values: Dict[str, Any] = {}
    for name, field_type in get_type_hints(Config).items():
        if name == "model_config":
            continue
        if name not in raw:
            raise RuntimeError(f"Missing required config key '{name}' in {_CONFIG_FILE}")
        values[name] = _coerce(name, raw[name], field_type)

    models = _parse_models(raw.get("model_config"))
    for key in ("using_model", "using_multimodal", "using_compress_model"):
        if raw[key] not in models:
            raise RuntimeError(f"config key '{key}' = '{raw[key]}' not found in model_config")

    return Config(**values, model_config=models)


config: Config = _parse(json.loads(_CONFIG_FILE.read_text(encoding="utf-8")))


def reload_config() -> None:
    fresh = _parse(json.loads(_CONFIG_FILE.read_text(encoding="utf-8")))
    for field in fields(Config):
        object.__setattr__(config, field.name, getattr(fresh, field.name))
