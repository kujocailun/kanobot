"""
kanobot — 用户凭据存储
管理水鱼 Import-Token 绑定（仅个人 bot 使用，数据存本地 JSON）
"""
import json
import os

STORE_PATH = os.path.join(os.path.dirname(__file__), "data", "bindings.json")


def _load() -> dict:
    if not os.path.exists(STORE_PATH):
        return {}
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict):
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def bind_user(qq: str, username: str, token: str):
    """绑定 QQ 号 → 水鱼 Import-Token"""
    data = _load()
    data[qq] = {"username": username, "token": token}
    _save(data)


def unbind_user(qq: str):
    """解除绑定"""
    data = _load()
    data.pop(qq, None)
    _save(data)


def get_token(qq: str) -> tuple[str, str] | None:
    """获取 QQ 号对应的 (username, import_token)，未绑定返回 None"""
    data = _load()
    entry = data.get(qq)
    if entry and entry.get("username") and entry.get("token"):
        return entry["username"], entry["token"]
    return None
