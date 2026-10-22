"""Transcript adapters。目前只有 agy；新增平台時加一個模組並提供 load / extract_prompt / extract_tool_calls。"""
import importlib
from . import agy
from .agy import TranscriptFormatError


def get_adapter(platform="agy"):
    """依 platform 名稱載入對應的 transcript adapter 模組。"""
    if not platform or platform == "agy":
        return agy
    try:
        mod = importlib.import_module(f".{platform}", __name__)
    except ImportError as e:
        raise TranscriptFormatError(f"找不到平台 {platform!r} 的 transcript adapter：{e}") from None
    for fn in ("load", "extract_prompt", "extract_tool_calls"):
        if not callable(getattr(mod, fn, None)):
            raise TranscriptFormatError(f"平台 {platform!r} 的 adapter 缺少 {fn}() 函式")
    return mod


__all__ = ["agy", "TranscriptFormatError", "get_adapter"]
