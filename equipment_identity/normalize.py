"""型号、厂商编码与名称的规范化。

规范化只做机械等价（全角/半角、连字符变体、大小写、空白），
不做语义判断：旧商标、别名是否指向同一设备由人工决定确认，
名称相似只用于生成候选，绝不直接合并。
"""
from __future__ import annotations

import difflib
import unicodedata

# 常见连字符/破折号变体，统一为 ASCII 连字符。
_DASHES = {
    "‐", "‑", "‒", "–", "—", "―",
    "−", "⁃", "﹘", "﹣", "－",
}


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def normalize_model(text: str) -> str:
    """规范化型号：全角转半角、统一连字符、去除空白、统一大写。"""
    if not text:
        return ""
    s = _nfkc(text)
    s = "".join("-" if ch in _DASHES else ch for ch in s)
    s = "".join(s.split())
    return s.upper()


def normalize_vendor_code(code: str | None) -> str | None:
    """规范化厂商编码；空值返回 None（空编码不参与实体键）。"""
    s = _nfkc(code or "").strip().upper()
    return s or None


def normalize_text(text: str) -> str:
    """标题、参数名等文本的规范化：全角转半角并压缩空白。"""
    return " ".join(_nfkc(text).split())


def model_similarity(a: str, b: str) -> float:
    """型号相似度，仅用于候选打分，不作为合并依据。"""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()
