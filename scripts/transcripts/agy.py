"""agy（Antigravity）transcript_full.jsonl 的讀取與解析。只用標準函式庫。"""
import json

KNOWN_TYPES = {"SYSTEM_MESSAGE", "PLANNER_RESPONSE"}


class TranscriptFormatError(Exception):
    """transcript 格式無法辨識。訊息會原樣寫進稽核結果。"""


def decode_arg(value):
    """transcript 中的工具參數常是二次編碼的 JSON 字串，例如 "\"abc\""。"""
    if isinstance(value, str):
        s = value.strip()
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return s[1:-1]
    return value


def load(path):
    """讀 JSONL。不是 JSONL、或沒有任何可辨識的 entry type 時拋 TranscriptFormatError。"""
    entries = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise TranscriptFormatError(f"transcript 不是 JSONL：第 {n} 行無法解析（{e.msg}）") from None
    if not entries:
        raise TranscriptFormatError("transcript 是空檔，沒有任何 entry")
    if not any(isinstance(e, dict) and e.get("type") in KNOWN_TYPES for e in entries):
        raise TranscriptFormatError(
            "transcript 格式無法辨識：找不到任何 SYSTEM_MESSAGE 或 PLANNER_RESPONSE entry")
    return entries


def extract_prompt(entries):
    for e in entries:
        if not isinstance(e, dict) or e.get("type") != "SYSTEM_MESSAGE":
            continue
        c = e.get("content")
        if not isinstance(c, str):
            continue
        msg = c.find("[Message]")
        i = c.find("content=", msg if msg >= 0 else 0)
        if i < 0:
            i = c.find("content=")
        if i < 0:
            continue
        start = i + len("content=")
        end = c.rfind("</SYSTEM_MESSAGE>")
        return c[start:end] if end > start else c[start:]
    return None


def normalize_args(raw):
    """args 可能是 dict，也可能整包是一串 JSON 字串；無法解析時回傳 {"_unparsed": 原值}。"""
    if isinstance(raw, dict):
        return {k: decode_arg(v) for k, v in raw.items()}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_unparsed": raw}
        if isinstance(parsed, dict):
            return {k: decode_arg(v) for k, v in parsed.items()}
        return {"_unparsed": raw}
    if raw is None:
        return {}
    return {"_unparsed": raw}


def extract_tool_calls(entries):
    calls = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        for tc in e.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            calls.append({"step": e.get("step_index"), "name": tc.get("name"),
                          "args": normalize_args(tc.get("args"))})
    return calls
