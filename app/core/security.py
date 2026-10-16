"""API Key 生成与验证（SHA-256 哈希）。"""

import hashlib
import hmac
import secrets


def generate_api_key() -> tuple[str, str]:
    """生成 API Key，返回 (raw_key, key_hash)。
    仅显示一次，调用方必须立即保存 raw_key。
    """
    raw = "vcs_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, key_hash


def hash_api_key(raw_key: str) -> str:
    """对原始 API Key 做 SHA-256 哈希，用于数据库索引查询。"""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def verify_api_key(raw_key: str, key_hash: str) -> bool:
    """常量时间比较，防止 timing attack。"""
    raw_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return hmac.compare_digest(raw_hash.encode(), key_hash.encode())
