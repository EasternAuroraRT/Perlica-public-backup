import jwt
import time
import requests
from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import config

_ALLOWED_DAILY_DAYS = (3, 7, 10, 15, 30)

# ------------------------------------------------------------
# 内部私有函数 —— JWT 鉴权与底层 API 调用
# ------------------------------------------------------------

def _generate_qweather_jwt():
    private_key = load_pem_private_key(config.weather_private_key_pem, password=None)
    current_time = int(time.time())
    payload = {
        "sub": config.weather_project_id,
        "iat": current_time - 30,
        "exp": current_time + 60
    }
    headers = {
        "alg": "EdDSA",
        "kid": config.weather_credential_id
    }
    token = jwt.encode(
        payload=payload,
        key=cast(Ed25519PrivateKey, private_key),
        algorithm="EdDSA",
        headers=headers
    )
    return token


def _get_authorization_header() -> dict:
    """获取包含JWT的请求头"""
    token = _generate_qweather_jwt()
    return {"Authorization": f"Bearer {token}"}


def _call_api(endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """对和风天气API发起GET请求, 并解析结果"""
    host_addr = config.weather_host
    if not host_addr.startswith("http"):
        host_addr = f"https://{host_addr}"
    url = f"{host_addr}{endpoint}"
    headers = _get_authorization_header()
    if params is None:
        params = {}
    resp = requests.get(url, params=params, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "200":
        raise RuntimeError(f"API错误: {data.get('code')} – {data}")
    return data


# ------------------------------------------------------------
# 公共接口 ① —— 地理信息查询
# ------------------------------------------------------------

def get_locations(city_name: str) -> list[dict]:
    data = _call_api("/geo/v2/city/lookup", {"location": city_name})
    locations = data.get("location", [])
    if not locations:
        raise ValueError(f"未找到地点: {city_name}")
    return locations


# ------------------------------------------------------------
# 公共接口 ② —— 通过 LocationID 查询天气（细粒度控制）
# ------------------------------------------------------------

def get_current_weather_by_id(location_id: str) -> dict:
    data = _call_api("/v7/weather/now", {"location": location_id})
    return data.get("now", {})


def get_daily_weather_by_id(location_id: str, days: int = 7) -> list[dict]:
    if days not in _ALLOWED_DAILY_DAYS:
        raise ValueError(f"days 只能是 {list(_ALLOWED_DAILY_DAYS)} 之一，当前为 {days}")
    endpoint = f"/v7/weather/{days}d"
    data = _call_api(endpoint, {"location": location_id})
    return data.get("daily", [])


# ------------------------------------------------------------
# 公共接口 ③ —— 通过城市/地点名称查询天气（便捷封装）
# ------------------------------------------------------------

def get_current_weather(city_name: str) -> dict:
    locations = get_locations(city_name)
    return get_current_weather_by_id(locations[0]["id"])


def get_daily_weather(city_name: str, days: int = 7) -> list[dict]:
    locations = get_locations(city_name)
    return get_daily_weather_by_id(locations[0]["id"], days)


# ------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------

if __name__ == "__main__":
    try:
        # 1) 查看所有匹配地点
        locs = get_locations("北京")
        print(f"`北京`匹配到 {len(locs)} 个地点（第一个最匹配）:")
        for loc in locs:
            print(f"  - {loc.get('name')} ({loc.get('adm1')}) id={loc['id']}")
        print(f"原始数据: {locs}")

        # 2) 通过名称直接查天气
        current = get_current_weather("上海")
        print(f"\n上海当前天气: {current}")

        daily = get_daily_weather("成都", days=3)
        print(f"\n成都未来 {len(daily)} 天预报:")
        for d in daily:
            print(f"  {d.get('fxDate')}: {d.get('textDay')} {d.get('tempMin')}~{d.get('tempMax')}°C")
        print(f"原始数据：{daily}")

        # 3) 手动指定 location_id（例如当用户有多个匹配地点想精确选择时）
        target_id = locs[0]["id"]
        daily_by_id = get_daily_weather_by_id(target_id, days=7)
        print(f"\n通过 id={target_id} 查到 {len(daily_by_id)} 天预报")
    except Exception as e:
        print(f"查询失败: {e}")
