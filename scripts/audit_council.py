#!/usr/bin/env python3
"""
Agentic Council 機械式稽核腳本

用法：
    python audit_council.py --skill-dir <skill 資料夾> --run-dir <本次執行資料夾>

run-dir 內必須有：
    brief.md     凍結簡報全文（主 Agent 派發前寫入，派發時以此檔內容組裝任務）
    seats.json   席位對照表，格式：
        {
          "parent_id": "<主 Agent 對話 ID>",
          "seats": [
            {"seat": 1, "conversation_id": "<子代理 ID>", "transcript": "<transcript.jsonl 路徑或 file:/// URI>"},
            {"seat": 3, "conversation_id": "<重派的子代理 ID>", "transcript": "...", "retry": true},
            ...
          ]
        }

輸出：
    run-dir/audit.json   完整結果（機器可讀）
    標準輸出              Markdown 摘要（貼入執行狀態列與評分總表）

只使用 Python 標準函式庫。
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcripts import agy as tx_adapter  # noqa: E402
from transcripts.agy import TranscriptFormatError  # noqa: E402

# 供外部（含測試）沿用舊名稱
load_transcript = tx_adapter.load
extract_prompt = tx_adapter.extract_prompt
extract_tool_calls = tx_adapter.extract_tool_calls

ALLOWED_TOOLS = {"search_web", "read_url_content"}
STANCES = ["支持推進", "有條件推進", "要求推倒重構", "強烈否決"]
DIMENSIONS = ["生存安全", "上限潛力", "執行可行性", "長期穩定", "假設穩健度", "人因可持續性"]
REQUIRED_FIELDS = ["【席位】", "【立場裁定】", "【信心程度】", "【六維評分】", "【必答回應】",
                   "【最強支持理由】", "【致命盲點】", "【防禦性底線】"]
# 字數建議上限（D2）：超過只記警告。A14 會加上 --char-limit 覆寫。
CHAR_LIMIT_DEFAULT = 1200
# 席位 5（逆向破局者）的職責比其他席位多一整段「完全相反的做法＋其最強論證」，
# 用同一個上限會系統性地把這一席判出局，因此單獨放寬。
CHAR_LIMIT_BY_SEAT = {5: 1500}
MUST_ANSWER_MIN = 2      # 【必答回應】至少要答的題數（D6）
ANCHOR_MIN_SEATS = 2     # 幾位評審用到同一個題目用詞，就列入錨定清單（D7）
ANCHOR_MAX_TERMS = 20    # 錨定清單最多列幾筆
ANCHOR_CJK_MIN, ANCHOR_CJK_MAX = 4, 12   # CJK 候選詞長度範圍（連續 CJK 字元的子字串）
ANCHOR_WORD_MIN = 3      # 英數單字候選詞最短長度
RETRY_HINT_MAX = 2       # 重派任務最多附幾條 retry_menu.md 條目（D5）


def build_char_limits(general=None, seat5=None):
    """--char-limit N 覆寫一般席位上限，席位 5 依 1500/1200 比例放寬；--char-limit-seat5 M 直接指定席位 5。"""
    limits = {"default": CHAR_LIMIT_DEFAULT, **CHAR_LIMIT_BY_SEAT}
    if general is not None:
        limits["default"] = general
        for k, v in CHAR_LIMIT_BY_SEAT.items():
            limits[k] = round(general * v / CHAR_LIMIT_DEFAULT)
    if seat5 is not None:
        limits[5] = seat5
    return limits


def char_limit(seat_no, limits=None):
    limits = limits or build_char_limits()
    return limits.get(seat_no, limits["default"])
TEAR_THRESHOLD = 5
PROB_VALUES = ("高", "中", "低")
IMPACT_VALUES = ("致命", "重大", "可控")
BASIS_VALUES = ("[事實]", "[外部來源]", "[推測]", "[假設]")


def role_name(role_text):
    """角色卡首行的「XXX」就是該席位應自報的名稱。"""
    m = re.search(r"「(.+?)」", role_text)
    return m.group(1) if m else None


def norm(text):
    """統一換行並去除首尾空白，避免 CRLF/LF 與檔尾空行造成誤判。"""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def to_path(ref):
    if ref.startswith("file:"):
        p = unquote(urlparse(ref).path)
        if re.match(r"^/[A-Za-z]:/", p):   # Windows: /C:/Users/... -> C:/Users/...
            p = p[1:]
        return Path(p)
    return Path(ref)


def load_menu(skill_dir, prefix="R"):
    """解析 templates/retry_menu.md 的條目：{編號: 條目全文}。讀不到回傳空表。"""
    try:
        text = (Path(skill_dir) / "templates" / "retry_menu.md").read_text(encoding="utf-8")
    except Exception:
        return {}
    out = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(rf"^- ({prefix}\d+)\s+(.+)$", line.rstrip())
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2).strip()
    return out


def retry_info(seat):
    """回傳 None（非重派）或 {"hint_ids": [...], "valid": bool}。"""
    r = seat.get("retry")
    if r is True:
        return {"hint_ids": [], "valid": True}
    if isinstance(r, dict):
        ids = r.get("hint_ids", [])
        ok = isinstance(ids, list) and all(isinstance(i, str) for i in ids)
        return {"hint_ids": ids if ok else [], "valid": ok}
    return None


def check_retry_residual(residual, hint_ids, menu):
    """重派殘留必須依序等於 hint_ids 對應的選單條目。回傳錯誤訊息清單。"""
    errs = []
    if len(hint_ids) > RETRY_HINT_MAX:
        errs.append(f"重派附了 {len(hint_ids)} 個提示編號，超過上限 {RETRY_HINT_MAX}")
    unknown = [i for i in hint_ids if i not in menu]
    if unknown:
        errs.append(f"hint_ids 不在 retry_menu.md：{', '.join(unknown)}")
    if errs:
        return errs
    expected = [menu[i] for i in hint_ids]
    actual = [l.strip() for l in (residual or "").split("\n") if l.strip()]
    if actual != expected:
        errs.append("重派任務的額外文字與 hint_ids 對應的 retry_menu.md 條目不符"
                    f"（預期 {len(expected)} 行選單原文，實際 {len(actual)} 行：{' / '.join(actual)[:100]}）")
    return errs


def role_sections(role_text):
    """切出角色卡「必答問題：」與「禁止：」兩段（到下一個空行為止）。"""
    out = []
    lines = norm(role_text).split("\n")
    for i, l in enumerate(lines):
        if l.strip() in ("必答問題：", "禁止：", "必答問題:", "禁止:"):
            seg = []
            for x in lines[i + 1:]:
                if not x.strip():
                    break
                seg.append(x)
            out.append("\n".join(seg))
    return out


def check_role_check(seat, role_text):
    """回傳 (excluded, error_or_None)。只有 pass is False 才生效。"""
    rc = seat.get("role_check")
    if not isinstance(rc, dict) or rc.get("pass") is not False:
        return False, None
    q = rc.get("quote")
    n = seat.get("seat")
    if not isinstance(q, str) or not norm(q):
        return False, f"席位 {n} 的 role_check 缺少 quote，已忽略此排除"
    # 必須引用完整的一條，不接受片段：否則引用「風險」兩個字就能排除任何一席。
    items = {re.sub(r"^[-*]\s*", "", l.strip()) for sec in role_sections(role_text) for l in sec.split("\n")}
    if re.sub(r"^[-*]\s*", "", norm(q)) in items:
        return True, None
    return False, f"席位 {n} 的 role_check.quote 不是角色卡「必答問題」或「禁止」段落中完整的一條，已忽略此排除"


def check_prompt(prompt, brief, role, schema):
    issues = []
    if prompt is None:
        return ["transcript 中找不到派發任務內容"], None
    p = norm(prompt)
    for label, piece in (("簡報", brief), ("角色卡", role), ("輸出規格", schema)):
        if piece not in p:
            issues.append(f"任務中找不到逐字相符的{label}")
    if not p.startswith(brief):
        issues.append("任務開頭不是凍結簡報")
    residual = p
    for piece in (brief, role, schema):
        residual = residual.replace(piece, "", 1)
    residual = residual.strip()
    return issues, (residual or None)


def split_fields(r):
    """以欄位標記切出各段內容，供非空檢查與逐段驗證使用。"""
    out, idx = {}, []
    for f in REQUIRED_FIELDS:
        i = r.find(f)
        if i >= 0:
            idx.append((i, f))
    idx.sort()
    for n, (i, f) in enumerate(idx):
        end = idx[n + 1][0] if n + 1 < len(idx) else len(r)
        out[f] = r[i + len(f):end].strip()
    return out


def check_blind_spots(section):
    """逐個致命盲點檢查枚舉值與來源網址，而不是全文計數。回傳 (盲點數, 錯誤, 標 [外部來源] 的盲點數)。"""
    errors, external = [], 0
    blocks = [b for b in re.split(r"場景\s*[:：]", section)[1:]]
    for n, b in enumerate(blocks, 1):
        m = re.search(r"發生機率\s*[:：]\s*([^\s｜|，,。]+)", b)
        if not m:
            errors.append(f"盲點 {n} 缺少「發生機率」")
        elif m.group(1) not in PROB_VALUES:
            errors.append(f"盲點 {n} 的發生機率不是高/中/低：{m.group(1)}")
        m = re.search(r"衝擊\s*[:：]\s*([^\s｜|，,。]+)", b)
        if not m:
            errors.append(f"盲點 {n} 缺少「衝擊」")
        elif m.group(1) not in IMPACT_VALUES:
            errors.append(f"盲點 {n} 的衝擊不是致命/重大/可控：{m.group(1)}")
        m = re.search(r"論據性質\s*[:：]\s*(.+)", b)
        if not m:
            errors.append(f"盲點 {n} 缺少「論據性質」")
        else:
            basis = m.group(1)
            if not any(v in basis for v in BASIS_VALUES):
                errors.append(f"盲點 {n} 的論據性質不是四選一：{basis.strip()[:20]}")
            # [外部來源] 必須在「這一個」盲點內附網址，不能靠全文其他地方的網址充數
            if "[外部來源]" in basis:
                external += 1
                if not re.search(r"https?://", b):
                    errors.append(f"盲點 {n} 標註 [外部來源] 但該盲點內未附網址")
    return len(blocks), errors, external


def _head(body):
    """欄位內容去掉開頭的冒號、空白與粗體 **。"""
    return re.sub(r"^[\s*:：]+", "", body or "")


def field_order_warning(r):
    pos = [(r.find(f), f) for f in REQUIRED_FIELDS if r.find(f) >= 0]
    if [f for _, f in sorted(pos)] != [f for _, f in pos]:
        return "欄位順序與規格不同（僅警告）：" + "".join(f for _, f in sorted(pos))
    return None


def count_must_answers(body):
    """依 run_files.md 的 regex 計數不重複的 Q 題號（行首 Q<n>：後須有內容）。"""
    return len(set(re.findall(r"^\s*Q(\d+)\s*[:：]\s*\S", body or "", re.M)))


def validate_report(report, limit, expected_role=None, tools_used=None):
    errors, warnings = [], []
    result = {"stance": None, "scores": {}, "chars": 0, "must_answer_count": None}
    if not report:
        return ["沒有回傳任何報告"], warnings, result
    r = norm(report)
    for field in REQUIRED_FIELDS:
        if field not in r:
            errors.append(f"缺少欄位 {field}")
    w = field_order_warning(r)
    if w:
        warnings.append(w)
    fields = split_fields(r)
    for field, body in fields.items():
        if not body:
            errors.append(f"欄位 {field} 內容為空")
    if expected_role:
        seat_body = fields.get("【席位】", "")
        if seat_body and expected_role not in seat_body:
            errors.append(f"【席位】自報「{seat_body[:20]}」與角色卡「{expected_role}」不符")
    # 立場、信心、六維分數只在對應欄位內擷取（A9）
    if "【立場裁定】" in fields:
        body = _head(fields["【立場裁定】"])
        val = re.sub(r"\*", "", body.split("\n")[0]).strip() if body else ""
        if val in STANCES:
            result["stance"] = val
        elif body:
            errors.append(f"立場不是四選一：{val}")
    if "【信心程度】" in fields:
        body = re.sub(r"\*", "", _head(fields["【信心程度】"]))
        m = re.match(r"(高|中|低)(?![\u4e00-\u9fff])", body)
        if body and not m:
            errors.append("信心程度不是高/中/低")
        elif m and not re.sub(r"^[\s—\-－–:：,，、（()）]+", "", body[1:]).strip(" )）"):
            errors.append("信心程度缺少說明（格式：高/中/低 —— 一句話說明）")
    score_body = re.sub(r"\*", "", fields.get("【六維評分】", ""))
    for dim in DIMENSIONS:
        m = re.search(re.escape(dim) + r"\s*[:：]?\s*(\d+)\s*/\s*10", score_body)
        if not m:
            errors.append(f"缺少評分：{dim}")
            continue
        v = int(m.group(1))
        if not 1 <= v <= 10:
            errors.append(f"評分超出範圍：{dim}={v}")
        result["scores"][dim] = v
    if "【必答回應】" in fields:
        n = count_must_answers(fields["【必答回應】"])
        result["must_answer_count"] = n
        if n < MUST_ANSWER_MIN:
            errors.append(f"【必答回應】只有 {n} 題，至少要 {MUST_ANSWER_MIN} 題（格式 Q<n>：<回應>）")
    else:
        result["must_answer_count"] = 0
    n_scene, spot_errors, n_external = check_blind_spots(fields.get("【致命盲點】", ""))
    if not 1 <= n_scene <= 2:
        errors.append(f"致命盲點數量應為 1–2，實際 {n_scene}")
    errors += spot_errors
    if n_external and tools_used is not None and not any(t in ALLOWED_TOOLS for t in tools_used):
        errors.append("標註 [外部來源] 但 transcript 沒有任何 search_web 或 read_url_content 呼叫")
    chars = len(re.sub(r"\s", "", r))
    result["chars"] = chars
    if chars > limit:
        # 超長只記警告，不判缺席：丟掉一份欄位齊全、評分完整的報告，
        # 代價遠大於字數本身（實測兩次都是被丟掉的那幾席帶著當次最關鍵的事實）。
        warnings.append(f"篇幅 {chars} 字，超過 {limit} 字上限（僅警告，不影響判定）")
    return errors, warnings, result


def new_row(seat_no, conv, retried=False):
    return {"seat": seat_no, "conversation_id": conv,
            "verdict": None, "excluded_by_role_check": False, "role_check_reason": None, "retried": retried,
            "must_answer_count": None, "warnings": [],
            "isolation": [], "dispatch": [], "residual": None,
            "format_errors": [], "format_warnings": [], "tools_used": [],
            "stance": None, "scores": {}, "chars": 0, "report": ""}


DEFAULT_BRAIN_DIRS = ("~/.gemini/antigravity/brain", "~/.gemini/antigravity-cli/brain")


def _key(p):
    return os.path.normcase(str(Path(p).resolve()))


def check_transcript_path(path, conv, brain_dirs):
    """D3：路徑必須是 <brain>/<conversation_id>/.system_generated/logs/transcript_full.jsonl。

    回傳錯誤訊息或 None。只比對路徑字串，不讀檔。
    """
    if not isinstance(conv, str) or not conv or conv in (".", "..") or re.search(r"[\\/]", conv):
        return f"conversation_id 不合法：{conv!r}"
    try:
        actual = _key(path)
        for b in brain_dirs:
            if actual == _key(Path(b) / conv / ".system_generated" / "logs" / "transcript_full.jsonl"):
                return None
    except (OSError, ValueError) as e:
        return f"transcript 路徑無法解析：{e!r}"
    return (f"transcript 路徑不符：必須是 <brain>/{conv}/.system_generated/logs/transcript_full.jsonl"
            f"（實際 {str(path)[:120]}），未讀取該檔")


def audit_seat(seat, parent_id, brief, roles, schema, menu=None, config_errors=None,
               brain_dirs=None, char_limits=None):
    menu = menu or {}
    brain_dirs = list(brain_dirs) if brain_dirs is not None else [os.path.expanduser(d) for d in DEFAULT_BRAIN_DIRS]
    config_errors = config_errors if config_errors is not None else []
    retry = retry_info(seat)
    out = new_row(seat["seat"], seat.get("conversation_id"), seat.get("retry") not in (None, False))
    out["warnings"] = out["format_warnings"]      # 同一個清單，之後的警告自動同步
    try:
        out["excluded_by_role_check"], err = check_role_check(seat, roles.get(seat["seat"], ""))
        if out["excluded_by_role_check"]:
            out["role_check_reason"] = str(seat["role_check"].get("reason") or "")
        if err:
            config_errors.append(err)
    except Exception as e:
        config_errors.append(f"席位 {seat['seat']} 的 role_check 無法解析：{e!r}")
    try:
        path = to_path(seat["transcript"])
        perr = check_transcript_path(path, seat.get("conversation_id"), brain_dirs)
        if perr:
            out["verdict"] = "未稽核"
            out["isolation"].append(perr)
            return out
        entries = tx_adapter.load(path)
    except TranscriptFormatError as e:
        out["verdict"] = "未稽核"
        out["isolation"].append(str(e))
        return out
    except Exception as e:
        out["verdict"] = "未稽核"
        out["isolation"].append(f"無法讀取 transcript：{e!r}")
        return out

    issues, residual = check_prompt(extract_prompt(entries), brief, roles[seat["seat"]], schema)
    out["dispatch"], out["residual"] = issues, residual

    reports = []
    for c in extract_tool_calls(entries):
        out["tools_used"].append(c["name"])
        if c["name"] in ALLOWED_TOOLS:
            continue
        if c["name"] == "send_message":
            rcpt = c["args"].get("Recipient")
            if rcpt is None:
                out["isolation"].append(f"step {c['step']}：send_message 無法判斷收件對象")
            elif rcpt != parent_id:
                out["isolation"].append(f"step {c['step']}：send_message 發給非主 Agent 對象 {rcpt}")
            else:
                reports.append(c["args"].get("Message"))
            continue
        out["isolation"].append(f"step {c['step']}：使用禁止的工具 {c['name']}")

    if len(reports) > 1:
        out["format_warnings"].append(f"回傳了 {len(reports)} 則訊息，以最後一則為準")
    errs, warns, res = validate_report(reports[-1] if reports else None,
                                   char_limit(seat["seat"], char_limits),
                                   role_name(roles.get(seat["seat"], "")),
                                   out["tools_used"])
    out["report"] = reports[-1] if reports else ""
    out["format_errors"] += errs
    out["format_warnings"] += warns
    out.update(stance=res["stance"], scores=res["scores"], chars=res["chars"],
               must_answer_count=res["must_answer_count"])

    unknown_rcpt = any("無法判斷收件對象" in i for i in out["isolation"])
    hard_violation = any("無法判斷收件對象" not in i for i in out["isolation"])
    if out["retried"]:
        if retry is None or not retry["valid"]:
            out["dispatch"].append("seats.json 的 retry 格式不合法（hint_ids 必須是字串清單）")
        else:
            out["dispatch"] += check_retry_residual(residual, retry["hint_ids"], menu)
    elif residual:
        out["dispatch"].append("首次派發的任務含有簡報、角色卡、規格以外的文字")
    if out["dispatch"]:
        out["verdict"] = "派發錯誤"
    elif hard_violation:
        out["verdict"] = "隔離違規"
    elif out["format_errors"]:
        out["verdict"] = "格式不合格"
    elif unknown_rcpt:
        out["verdict"] = "未稽核"
    else:
        out["verdict"] = "通過"
    return out


def absent_row(seat_no, reason):
    """seats.json 中沒有登錄的席位，仍必須以缺席列出，不能從報表中消失。"""
    row = new_row(seat_no, None)
    row["verdict"] = "缺席"
    row["dispatch"] = [reason]
    return row


def validate_seats_config(cfg):
    """六席必須是六個不同的席位。重複登錄一律拒收，缺的席位補成缺席列。

    這是六席設計的基本前提：同一席重複六次不能形成六票。
    """
    errors, rows, seen_seat, seen_conv, seen_tx = [], [], {}, {}, {}
    for raw in cfg.get("seats") or []:
        if not isinstance(raw, dict):
            errors.append(f"席位登錄項目不是物件：{raw!r}")
            continue
        n = raw.get("seat")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 6:
            errors.append(f"席位編號不合法：{n!r}（必須是 1–6 的整數）")
            continue
        if n in seen_seat:
            errors.append(f"席位 {n} 重複登錄，第 2 筆以後一律不採計")
            continue
        conv = raw.get("conversation_id")
        if conv and conv in seen_conv:
            errors.append(f"席位 {n} 與席位 {seen_conv[conv]} 使用同一個對話 ID {conv}")
            continue
        tx = raw.get("transcript")
        try:
            key = str(to_path(tx).resolve()).lower() if tx and isinstance(tx, str) else None
        except (OSError, ValueError):
            key = str(tx).lower()
        if key and key in seen_tx:
            errors.append(f"席位 {n} 與席位 {seen_tx[key]} 指向同一份 transcript")
            continue
        seen_seat[n] = raw
        if conv:
            seen_conv[conv] = n
        if key:
            seen_tx[key] = n
        rows.append(raw)
    missing = [n for n in range(1, 7) if n not in seen_seat]
    return sorted(rows, key=lambda x: x["seat"]), missing, errors


def aggregate(seats):
    seen, deduped = set(), []
    for s in seats:
        if s["seat"] in seen:      # 同一席位只能計一次票
            continue
        seen.add(s["seat"])
        deduped.append(s)
    valid = [s for s in deduped if s["verdict"] == "通過" and len(s["scores"]) == 6
             and not s.get("excluded_by_role_check")]
    dist = {k: sum(1 for s in valid if s["stance"] == k) for k in STANCES}
    tears = []
    for dim in DIMENSIONS:
        vals = [(s["scores"][dim], s["seat"]) for s in valid]
        if len(vals) >= 2:
            hi, lo = max(vals), min(vals)
            if hi[0] - lo[0] >= TEAR_THRESHOLD:
                tears.append({"dimension": dim, "high": hi, "low": lo, "gap": hi[0] - lo[0]})
    return {"valid_seats": [s["seat"] for s in valid], "stance_distribution": dist, "tears": tears}


def cell(text):
    """表格儲存格內：| 跳脫成 \\|、換行改成空白。"""
    return str(text).replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def to_markdown(seats, agg, residual_note):
    lines = ["### 機械式稽核結果", "",
             "| 席位 | 判定 | 使用工具 | 字數 | 問題 |", "|---|---|---|---|---|"]
    for s in seats:
        problems = [cell(p) for p in seat_problems(s)]
        lines.append(f"| {s['seat']} | {s['verdict']} | {', '.join(s['tools_used']) or '—'} | "
                     f"{s['chars'] or '—'} | {'；'.join(problems) or '—'} |")
    lines += ["", "### 六維評分總表", "",
              "| 席位 | " + " | ".join(DIMENSIONS) + " | 立場 |",
              "|---" * (len(DIMENSIONS) + 2) + "|"]
    for s in seats:
        ok = s["seat"] in agg["valid_seats"]
        row = [str(s["scores"].get(d, "—")) if ok else "—" for d in DIMENSIONS]
        lines.append(f"| {s['seat']} | " + " | ".join(row) + f" | {s['stance'] if ok else '—'} |")
    lines += ["", "### 統計", "",
              "立場分布：" + "、".join(f"{k} {v} 席" for k, v in agg["stance_distribution"].items())]
    if agg["tears"]:
        lines.append("評分撕裂（分差 ≥ 5）：" + "；".join(
            f"{t['dimension']} 席位{t['high'][1]}={t['high'][0]} vs 席位{t['low'][1]}={t['low'][0]}（差 {t['gap']}）"
            for t in agg["tears"]))
    else:
        lines.append("評分撕裂（分差 ≥ 5）：無")
    if residual_note:
        lines += ["", residual_note]
    return "\n".join(lines)


# 數值擷取（A10）。流程：NFKC 正規化 -> 以等長空白蓋掉網址、日期、清單編號 -> 比對數值＋單位。
# 負號只在前面不是英數或小數點時才算（3-5 天 是範圍，不是 -5）；Q1、R2、v2.5 這類編號不算數值。
_NUM = (r"(?<![A-Za-z\d.,])(?P<neg>(?<![\w.])[-\u2212])?"
        r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
_CN_NUM = r"[一二兩三四五六七八九十百千]+"
# 順序即優先序：長的在前（日圓 先於 日、個月 先於 個、分鐘 先於 分、萬元 先於 萬）
_UNIT = (r"(?:%|GB|MB|TB|KB|kg|cm|mm|ms|km|公尺|公里|小時|分鐘|分|秒|天|日圓|日|週|周|個月|個|月|年|次|席|條|"
         r"美元|円|萬元|億元|元|萬|億|人|名|組|顆|倍|吋|度|件|台|家|位|項|筆|張|份|套|輛|間|場|頁|行|字|[kK](?![A-Za-z]))")
VALUE_PAT = re.compile(
    rf"(?P<cur>NT\$|US\$|\$)?{_NUM}(?:[ \t]*(?P<unit>{_UNIT}))?"
    rf"|(?P<cn>{_CN_NUM})[ \t]*(?P<cnunit>{_UNIT})")
_NOISE_PATS = [
    re.compile(r"https?://[^\s)）」』>\]]+"),                                   # 網址
    re.compile(r"\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*[日號]?"),     # 2026-10-19、2026/10/19、2026年10月19日
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*[日號]"),                              # 10月19日
    re.compile(r"(?m)^[ \t]*(?:[-*+][ \t]+)?\d+[.)、][ \t]+"),                   # 條列編號
    re.compile(r"(?m)^#{1,6}[ \t]*\d+[.、)]"),                                   # 標題編號
]
_CN_SOFT = {"一"}                       # 「一次」「一個」是慣用語，只有時間單位才算數值
_CN_SOFT_OK_UNITS = {"週", "周", "天", "個月", "月", "年", "小時", "分鐘", "秒"}
_UNIT_ALIAS = {"周": "週", "円": "日圓", "K": "k"}


def clean_for_numbers(text):
    t = unicodedata.normalize("NFKC", text or "")
    for pat in _NOISE_PATS:
        t = pat.sub(lambda m: " " * len(m.group(0)), t)
    return t


def _ctx(t, a, b, width=15):
    return re.sub(r"\s+", " ", t[max(0, a - width):b + width]).strip()


def extract_numbers(text):
    """回傳 [{"value": 原文, "key": (數值, 單位), "context": 前後約 15 字}]，依出現順序。"""
    original = unicodedata.normalize("NFKC", text or "")
    cleaned = clean_for_numbers(text)
    out = []
    for m in VALUE_PAT.finditer(cleaned):
        raw = re.sub(r"\s+", " ", m.group(0).strip())
        if m.group("cn"):
            unit = m.group("cnunit")
            if m.group("cn") in _CN_SOFT and unit not in _CN_SOFT_OK_UNITS:
                continue
            key = (m.group("cn"), _UNIT_ALIAS.get(unit, unit))
        else:
            unit = (m.group("cur") or "") + (m.group("unit") or "")
            digits = m.group("num").replace(",", "")
            if not m.group("unit") and not m.group("cur") and re.fullmatch(r"\d", digits):
                continue          # 跳過條列編號之類的單一數字
            try:
                n = format(Decimal(digits).normalize(), "f")
            except InvalidOperation:
                continue
            if m.group("neg"):
                n = "-" + n
            u = m.group("unit") or ""
            key = (n, (m.group("cur") or "") + _UNIT_ALIAS.get(u, u))
        out.append({"value": raw, "key": key, "context": _ctx(original, m.start(), m.end())})
    return out


def strip_score_line(report):
    """六維評分那行是規格固定欄位（x/10），不是方案數值。"""
    return "\n".join(l for l in norm(report or "").split("\n") if "【六維評分】" not in l)


def eligible(s):
    return s.get("verdict") == "通過" and not s.get("excluded_by_role_check")


def collect_values(seats):
    """列出各席「稽核通過」報告中出現過的數值（含單位）。

    供步驟 4 比對加固方案用。只收通過且未被 role_check 排除的席位——
    未稽核席位的報告沒有經過隔離與格式驗證，它的數值不能充當加固方案的合法來源。
    不改變任何稽核判定，只是把人工比對的工作變成可機械核對的清單。
    """
    out = {}
    for s in seats:
        if not eligible(s):
            continue
        vals = []
        for n in extract_numbers(strip_score_line(s.get("report", ""))):
            if n["value"] not in vals:
                vals.append(n["value"])
        out[s["seat"]] = vals
    return out


def check_plan_numbers(plan_text, brief_text, seats):
    """回傳 (unsourced, sourced)。每筆 {"value","context"}；sourced 另附 "source"。

    來源 = brief.md + 通過且未排除席位的報告。比對以（正規化數值, 單位）為準。
    """
    index = {}
    for n in extract_numbers(brief_text):
        index.setdefault(n["key"], ("brief.md", n["context"]))
    for s in seats:
        if eligible(s):
            for n in extract_numbers(strip_score_line(s.get("report", ""))):
                index.setdefault(n["key"], (f"席位 {s['seat']}", n["context"]))
    unsourced, sourced, seen = [], [], set()
    for n in extract_numbers(plan_text):
        if n["key"] in seen:
            continue
        seen.add(n["key"])
        if n["key"] in index:
            sourced.append({"value": n["value"], "context": n["context"], "source": index[n["key"]][0]})
        else:
            unsourced.append({"value": n["value"], "context": n["context"]})
    return unsourced, sourced


def plan_markdown(unsourced, sourced):
    lines = ["### 方案數字來源核對（--check-plan）", ""]
    if unsourced:
        lines += [f"**{len(unsourced)} 個數字在簡報與通過席位報告中找不到來源**，必須改寫成待確認的驗證步驟後重跑：", ""]
        lines += [f"- `{u['value']}`：…{cell(u['context'])}…" for u in unsourced]
    else:
        lines.append("所有數字都找得到來源。")
    if sourced:
        lines += ["", "有來源的數字：", ""]
        lines += [f"- `{u['value']}`（{u['source']}）：…{cell(u['context'])}…" for u in sourced]
    return "\n".join(lines)


def run_check_plan(run):
    audit_p, plan_p, brief_p = run / "audit.json", run / "plan.md", run / "brief.md"
    for p, hint in ((plan_p, "步驟 4 寫完方案後要先存成 plan.md"),
                    (audit_p, "請先不帶 --check-plan 跑一次稽核"),
                    (brief_p, "")):
        if not p.is_file():
            print(f"錯誤：找不到 {p}。{hint}", file=sys.stderr)
            return 2
    audit = json.loads(audit_p.read_text(encoding="utf-8"))
    unsourced, sourced = check_plan_numbers(plan_p.read_text(encoding="utf-8"),
                                            brief_p.read_text(encoding="utf-8"), audit.get("seats", []))
    audit["unsourced_numbers"] = unsourced
    audit_p.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(plan_markdown(unsourced, sourced))
    return 1 if unsourced else 0


# ---------- Mode B 輸出（A15）----------
def seat_problems(s):
    problems = s["dispatch"] + s["isolation"] + s["format_errors"] + s["format_warnings"]
    if s["residual"]:
        problems.append("任務含額外內容（見 audit.json）")
    if s.get("excluded_by_role_check"):
        problems.append("離題（role_check），不計票")
    return problems


def _seat_label(s, roles):
    name = role_name((roles or {}).get(s["seat"], ""))
    return f"席位 {s['seat']}" + (f"（{name}）" if name else "")


def build_summary(seats, roles, table_md):
    """每席一段，直接擷取報告原句（立場、最強支持理由、致命盲點的「場景」），不改寫。"""
    lines = ["# 決策審查會摘要", ""]
    for s in seats:
        counted = eligible(s)
        status = s["verdict"] + ("" if counted else "（不計票）")
        if s.get("excluded_by_role_check"):
            status = f"{s['verdict']}；離題排除（不計票）"
        lines += [f"## {_seat_label(s, roles)}：{status}", ""]
        if not counted:
            reasons = seat_problems(s)
            lines.append("- 狀態：" + ("；".join(cell(x) for x in reasons) if reasons else "無可用報告"))
            lines.append("")
            continue
        f = split_fields(norm(s.get("report", "")))
        lines.append(f"- 立場：{s['stance'] or '—'}")
        lines.append(f"- 最強支持理由：{_head(f.get('【最強支持理由】', '')).strip() or '—'}")
        scenes = re.findall(r"場景\s*[:：]\s*(.+)", f.get("【致命盲點】", ""))
        for n, sc in enumerate(scenes, 1):
            lines.append(f"- 致命盲點 {n} 場景：{sc.strip()}")
        if not scenes:
            lines.append("- 致命盲點場景：—")
        lines.append("")
    lines += ["## 稽核表", "", table_md]
    return "\n".join(lines) + "\n"


def build_report_full(seats, roles, table_md):
    """六席完整報告原文（逐字，不改寫）與完整稽核表。"""
    lines = ["# 決策審查會完整報告", ""]
    for s in seats:
        lines += [f"## {_seat_label(s, roles)}：{s['verdict']}", ""]
        if s.get("excluded_by_role_check"):
            lines += ["（此席已被 role_check 判定離題，不計票）", ""]
        lines += [s.get("report") or "（無報告）", "", "---", ""]
    lines += ["## 完整稽核表", "", table_md]
    return "\n".join(lines) + "\n"


# ---------- 錨定清單（A12，D7）----------
# 詞的定義（確定性，只用標準函式庫）：
#   CJK 詞 = 簡報中某段「連續 CJK 字元」（標點、空白、換行都會切斷）的子字串，長度 4–12；
#   英數詞 = 簡報中的英數單字（[A-Za-z0-9]+，須含字母，轉小寫），長度 >= 3。
#   席位「使用」某詞 = 該詞也出現在該席報告（CJK 為同樣的連續子字串、英數為同一單字）。
#   排除：整個詞是任一角色卡或輸出規格的子字串（固定用詞，如六維名稱、欄位標記）；
#         英數詞出現在角色卡／規格中者亦排除。
#   只計 通過且未被 role_check 排除 的席位，>= ANCHOR_MIN_SEATS 席使用才列入。
#   去重：同一組席位下，被較長詞完全包含的短詞移除。
#   排序：席位數多者在前，其次詞長，其次字典序；最多 ANCHOR_MAX_TERMS 筆。
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{%d,}" % ANCHOR_CJK_MIN)
_WORD_RE = re.compile(r"[A-Za-z0-9]{%d,}" % ANCHOR_WORD_MIN)


def _cjk_grams(text):
    out = set()
    for run in _CJK_RUN.findall(text):
        for n in range(ANCHOR_CJK_MIN, min(ANCHOR_CJK_MAX, len(run)) + 1):
            for k in range(len(run) - n + 1):
                out.add(run[k:k + n])
    return out


def _words(text):
    return {w.lower() for w in _WORD_RE.findall(text) if re.search(r"[A-Za-z]", w)}


def compute_anchoring(brief, seats, fixed_text=""):
    fixed_text = unicodedata.normalize("NFKC", fixed_text or "")
    fixed_words = _words(fixed_text)
    b = unicodedata.normalize("NFKC", brief or "")
    brief_cjk = {t for t in _cjk_grams(b) if t not in fixed_text}
    brief_words = _words(b) - fixed_words
    users = {}
    for s in seats:
        if not eligible(s):
            continue
        body = unicodedata.normalize("NFKC", s.get("report", "") or "")
        for t in (_cjk_grams(body) & brief_cjk) | (_words(body) & brief_words):
            users.setdefault(t, []).append(s["seat"])
    cand = {t: tuple(sorted(set(v))) for t, v in users.items() if len(set(v)) >= ANCHOR_MIN_SEATS}
    kept = {t: sv for t, sv in cand.items()
            if not any(len(o) > len(t) and sv == osv and t in o for o, osv in cand.items())}
    ordered = sorted(kept.items(), key=lambda kv: (-len(kv[1]), -len(kv[0]), kv[0]))
    return [{"term": t, "seats": list(sv)} for t, sv in ordered[:ANCHOR_MAX_TERMS]]


def anchoring_markdown(terms):
    if not terms:
        return ""
    lines = ["", f"### 錨定詞（簡報用詞被 {ANCHOR_MIN_SEATS} 席以上沿用）", "",
             "| 詞 | 席位 |", "|---|---|"]
    lines += [f"| {cell(t['term'])} | {'、'.join(map(str, t['seats']))} |" for t in terms]
    return "\n".join(lines)


def values_markdown(values):
    lines = ["### 各席報告中出現過的數值", "",
             "步驟 4 用：加固方案裡的每一個數值，只能來自使用者輸入或下表。兩邊都查不到就**不准寫**，",
             "標註 `[主代理新增]` 也不能免除——該處必須改寫成待確認的驗證步驟。", "",
             "| 席位 | 數值 |", "|---|---|"]
    for seat in sorted(values):
        lines.append(f"| {seat} | {'、'.join(values[seat]) or '—'} |")
    allv = sorted({v for vs in values.values() for v in vs})
    lines += ["", "全席聯集：" + ("、".join(allv) if allv else "—")]
    return "\n".join(lines)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--strict", action="store_true",
                    help="有任何席位未通過或缺席時回傳非 0；預設維持 0（代表稽核程式本身執行完成）")
    ap.add_argument("--list-values", action="store_true",
                    help="另外輸出各席報告中出現過的數值清單（步驟 4 比對加固方案用）")
    ap.add_argument("--check-plan", action="store_true",
                    help="不重新稽核；讀既有 audit.json 與 plan.md，核對 plan 的數字是否有來源（有 unsourced 時結束碼非 0）")
    ap.add_argument("--brain-dir", action="append", default=None,
                    help="agy brain 資料夾（可重複）；預設 ~/.gemini/antigravity/brain 與 ~/.gemini/antigravity-cli/brain")
    ap.add_argument("--char-limit", type=int, default=None, metavar="N",
                    help="覆寫一般席位的字數上限（預設 %d）；席位 5 依 1500/1200 比例放寬。超過只記警告" % CHAR_LIMIT_DEFAULT)
    ap.add_argument("--char-limit-seat5", type=int, default=None, metavar="M",
                    help="直接指定席位 5 的字數上限（優先於 --char-limit 的比例放寬）")
    a = ap.parse_args()
    char_limits = build_char_limits(a.char_limit, a.char_limit_seat5)
    brain_dirs = [os.path.expanduser(d) for d in (a.brain_dir or DEFAULT_BRAIN_DIRS)]
    skill, run = Path(a.skill_dir), Path(a.run_dir)
    if a.check_plan:
        sys.exit(run_check_plan(run))

    brief = norm((run / "brief.md").read_text(encoding="utf-8"))
    schema = norm((skill / "schemas" / "subagent_output.md").read_text(encoding="utf-8"))
    roles = {}
    for f in sorted((skill / "roles").glob("0[1-6]-*.md")):
        roles[int(f.name[:2])] = norm(f.read_text(encoding="utf-8"))
    if sorted(roles) != [1, 2, 3, 4, 5, 6]:
        sys.exit(f"角色卡不完整：只找到席位 {sorted(roles)}")

    cfg = json.loads((run / "seats.json").read_text(encoding="utf-8"))
    rows, missing, cfg_errors = validate_seats_config(cfg)
    menu = load_menu(skill)
    seats = []
    for s in rows:
        try:
            seats.append(audit_seat(s, cfg.get("parent_id"), brief, roles, schema, menu, cfg_errors,
                                    brain_dirs, char_limits))
        except Exception as e:      # 單席問題只影響該席，不讓整批稽核中斷
            row = new_row(s.get("seat"), s.get("conversation_id"), s.get("retry") not in (None, False))
            row["verdict"] = "未稽核"
            row["isolation"].append(f"稽核該席時發生例外：{e!r}")
            seats.append(row)
    for n in missing:
        seats.append(absent_row(n, "seats.json 中沒有這一席的執行紀錄"))
    seats.sort(key=lambda x: x["seat"])
    agg = aggregate(seats)

    if cfg_errors:
        print("### 席位登錄錯誤")
        print()
        for e in cfg_errors:
            print("- " + e)
        print()

    residual_note = None
    if any(s["residual"] for s in seats):
        residual_note = "任務中的額外文字（重派席位已由程式比對 retry_menu.md 條目，不符者判派發錯誤，無須人工逐字確認）：\n" + "\n".join(
            f"- 席位 {s['seat']}{'（重派）' if s['retried'] else ''}：{s['residual'][:150]}{'…（完整內容見 audit.json）' if len(s['residual']) > 150 else ''}"
            for s in seats if s["residual"])

    # 簡報範本的段落標題（例如「剛性限制」「風險底線」）每份簡報都有，不算錨定
    try:
        brief_tpl = norm((skill / "templates" / "baseline_brief.md").read_text(encoding="utf-8"))
    except OSError:
        brief_tpl = ""
    fixed_text = "\n".join(list(roles.values()) + [schema, brief_tpl])
    payload = {"seats": seats, "aggregate": agg, "config_errors": cfg_errors,
               "anchoring_terms": compute_anchoring(brief, seats, fixed_text), "unsourced_numbers": []}
    if a.list_values:
        payload["reported_values"] = collect_values(seats)
    (run / "audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    table_md = to_markdown(seats, agg, residual_note) + anchoring_markdown(payload["anchoring_terms"])
    (run / "summary.md").write_text(build_summary(seats, roles, table_md), encoding="utf-8")
    (run / "report_full.md").write_text(build_report_full(seats, roles, table_md), encoding="utf-8")
    print(table_md)
    if a.list_values:
        print("\n" + values_markdown(payload["reported_values"]))
    if a.strict:
        failed = [x["seat"] for x in seats if x["verdict"] != "通過"]
        if failed or cfg_errors:
            sys.exit(f"strict：席位 {failed} 未通過；席位登錄錯誤 {len(cfg_errors)} 則")


if __name__ == "__main__":
    main()
