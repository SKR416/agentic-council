"""Transcript adapters。目前只有 agy；新增平台時加一個模組並提供 load / extract_prompt / extract_tool_calls。"""
from . import agy
from .agy import TranscriptFormatError

__all__ = ["agy", "TranscriptFormatError"]
