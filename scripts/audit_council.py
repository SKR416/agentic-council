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
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ALLOWED_TOOLS = {"search_web", "read_url_content"}
STANCES = ["支持推進", "有條件推進", "要求推倒重構", "強烈否決"]
DIMENSIONS = ["生存安全", "上限潛力", "執行可行性", "長期穩定", "假設穩健度", "人因可持續性"]
REQUIRED_FIELDS = ["【席位】", "【立場裁定】", "【信心程度】", "【六維評分】",
                   "【最強支持理由】", "【致命盲點】", "【防禦性底線】"]
# 字數建議上限（D2）：超過只記警告。A14 會加上 --char-limit 覆寫。
CHAR_LIMIT_DEFAULT = 1200
# 席位 5（逆向破局者）的職責比其他席位多一整段「完全相反的做法＋其最強論證」，
# 用同一個上限會系統性地把這一席判出局，因此單獨放寬。
CHAR_LIMIT_BY_SEAT = {5: 1500}
MUST_ANSWER_MIN = 2      # 【必答回應】至少要答的題數（D6）
ANCHOR_MIN_SEATS = 2     # 幾位評審用到同一個題目用詞，就列入錨定清單（D7）
RETRY_HINT_MAX = 2       # 重派任務最多附幾條 retry_menu.md 條目（D5）


def char_limit(seat_no):
    return CHAR_LIMIT_BY_SEAT.get(seat_no, CHAR_LIMIT_DEFAULT)
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


def load_transcript(path):
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def extract_prompt(entries):
    for e in entries:
        if e.get("type") == "SYSTEM_MESSAGE" and "content=" in e.get("content", ""):
            c = e["content"]
            start = c.index("content=", c.index("[Message]") if "[Message]" in c else 0) + len("content=")
            end = c.rfind("</SYSTEM_MESSAGE>")
            return c[start:end] if end > start else c[start:]
    return None


def normalize_args(raw):
    """args 可能是 dict，也可能整包是一串 JSON 字串；兩種都要能處理。

    無法解析時回傳 {"_unparsed": <原值>}，讓後續檢查把它當成「收件者不明」，
    而不是讓單一席位的格式異常中斷整批稽核。
    """
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
    """逐個致命盲點檢查枚舉值與來源網址，而不是全文計數。"""
    errors = []
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
            if "[外部來源]" in basis and not re.search(r"https?://", b):
                errors.append(f"盲點 {n} 標註 [外部來源] 但該盲點內未附網址")
    return len(blocks), errors


def validate_report(report, limit, expected_role=None):
    errors, warnings = [], []
    result = {"stance": None, "scores": {}, "chars": 0}
    if not report:
        return ["沒有回傳任何報告"], warnings, result
    r = norm(report)
    for field in REQUIRED_FIELDS:
        if field not in r:
            errors.append(f"缺少欄位 {field}")
    fields = split_fields(r)
    for field, body in fields.items():
        if not body:
            errors.append(f"欄位 {field} 內容為空")
    if expected_role:
        seat_body = fields.get("【席位】", "")
        if seat_body and expected_role not in seat_body:
            errors.append(f"【席位】自報「{seat_body[:20]}」與角色卡「{expected_role}」不符")
    m = re.search(r"【立場裁定】\s*([^\n]+)", r)
    if m:
        val = m.group(1).strip()
        if val in STANCES:
            result["stance"] = val
        else:
            errors.append(f"立場不是四選一：{val}")
    m = re.search(r"【信心程度】\s*(\S)", r)
    if m and m.group(1) not in "高中低":
        errors.append("信心程度不是高/中/低")
    for dim in DIMENSIONS:
        m = re.search(re.escape(dim) + r"\s*(\d+)\s*/\s*10", r)
        if not m:
            errors.append(f"缺少評分：{dim}")
            continue
        v = int(m.group(1))
        if not 1 <= v <= 10:
            errors.append(f"評分超出範圍：{dim}={v}")
        result["scores"][dim] = v
    n_scene, spot_errors = check_blind_spots(fields.get("【致命盲點】", ""))
    if not 1 <= n_scene <= 2:
        errors.append(f"致命盲點數量應為 1–2，實際 {n_scene}")
    errors += spot_errors
    chars = len(re.sub(r"\s", "", r))
    result["chars"] = chars
    if chars > limit:
        # 超長只記警告，不判缺席：丟掉一份欄位齊全、評分完整的報告，
        # 代價遠大於字數本身（實測兩次都是被丟掉的那幾席帶著當次最關鍵的事實）。
        warnings.append(f"篇幅 {chars} 字，超過 {limit} 字上限（僅警告，不影響判定）")
    return errors, warnings, result


def audit_seat(seat, parent_id, brief, roles, schema):
    out = {"seat": seat["seat"], "conversation_id": seat.get("conversation_id"),
           "verdict": None, "isolation": [], "dispatch": [], "residual": None,
           "format_errors": [], "format_warnings": [], "tools_used": [],
           "stance": None, "scores": {}, "chars": 0}
    path = to_path(seat["transcript"])
    try:
        entries = load_transcript(path)
    except Exception as e:
        out["verdict"] = "未稽核"
        out["isolation"].append(f"無法讀取 transcript：{e}")
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
                                   char_limit(seat["seat"]),
                                   role_name(roles.get(seat["seat"], "")))
    out["report"] = reports[-1] if reports else ""
    out["format_errors"] += errs
    out["format_warnings"] += warns
    out.update(stance=res["stance"], scores=res["scores"], chars=res["chars"])

    unknown_rcpt = any("無法判斷收件對象" in i for i in out["isolation"])
    hard_violation = any("無法判斷收件對象" not in i for i in out["isolation"])
    if residual and not seat.get("retry"):
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
    return {"seat": seat_no, "conversation_id": None, "verdict": "缺席",
            "isolation": [], "dispatch": [reason], "residual": None,
            "format_errors": [], "format_warnings": [], "tools_used": [],
            "stance": None, "scores": {}, "chars": 0, "report": ""}


def validate_seats_config(cfg):
    """六席必須是六個不同的席位。重複登錄一律拒收，缺的席位補成缺席列。

    這是六席設計的基本前提：同一席重複六次不能形成六票。
    """
    errors, rows, seen_seat, seen_conv, seen_tx = [], [], {}, {}, {}
    for raw in cfg.get("seats") or []:
        n = raw.get("seat")
        if not isinstance(n, int) or not 1 <= n <= 6:
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
            key = str(to_path(tx).resolve()).lower() if tx else None
        except OSError:
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
    valid = [s for s in deduped if s["verdict"] in ("通過", "未稽核") and len(s["scores"]) == 6]
    dist = {k: sum(1 for s in valid if s["stance"] == k) for k in STANCES}
    tears = []
    for dim in DIMENSIONS:
        vals = [(s["scores"][dim], s["seat"]) for s in valid]
        if len(vals) >= 2:
            hi, lo = max(vals), min(vals)
            if hi[0] - lo[0] >= TEAR_THRESHOLD:
                tears.append({"dimension": dim, "high": hi, "low": lo, "gap": hi[0] - lo[0]})
    return {"valid_seats": [s["seat"] for s in valid], "stance_distribution": dist, "tears": tears}


def to_markdown(seats, agg, residual_note):
    lines = ["### 機械式稽核結果", "",
             "| 席位 | 判定 | 使用工具 | 字數 | 問題 |", "|---|---|---|---|---|"]
    for s in seats:
        problems = s["dispatch"] + s["isolation"] + s["format_errors"] + s["format_warnings"]
        if s["residual"]:
            problems.append("任務含額外內容（見 audit.json）")
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


# 數值擷取：先處理千分位與負號，避免 1,000,000 被拆成兩筆；
# 中文數字（三週、十天）另行比對，否則會整個漏掉。
_NUM = r"[-－−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CN_NUM = r"[一二兩三四五六七八九十百千]+"
_UNIT = (r"(?:%|％|GB|MB|TB|KB|ms|km|公尺|公里|小時|分鐘|分|秒|天|日|週|周|個月|月|年|次|席|條|"
         r"美元|日圓|円|元|萬|億|人|名|組|顆|倍|吋|度)")
VALUE_PAT = re.compile(rf"(?:NT\$|US\$|\$)?{_NUM}\s*{_UNIT}?|{_CN_NUM}\s*{_UNIT}")


def collect_values(seats):
    """列出各席「稽核通過」報告中出現過的數值（含單位）。

    供步驟 4 比對加固方案用。只收通過的席位——未稽核席位的報告沒有經過
    隔離與格式驗證，它的數值不能充當加固方案的合法來源。
    不改變任何稽核判定，只是把人工比對的工作變成可機械核對的清單。
    """
    out = {}
    for s in seats:
        if s["verdict"] != "通過":
            continue
        # 六維評分那行是規格固定欄位（x/10），不是方案數值，先剔除
        lines_ = norm(s.get("report", "") or "").split(chr(10))
        body = chr(10).join(l for l in lines_ if "【六維評分】" not in l)
        vals = []
        for m in VALUE_PAT.finditer(body):
            v = re.sub(r"\s+", " ", m.group(0).strip())
            if re.fullmatch(r"\d", v):
                continue          # 跳過條列編號之類的單一數字
            if v not in vals:
                vals.append(v)
        out[s["seat"]] = vals
    return out


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
    a = ap.parse_args()
    skill, run = Path(a.skill_dir), Path(a.run_dir)

    brief = norm((run / "brief.md").read_text(encoding="utf-8"))
    schema = norm((skill / "schemas" / "subagent_output.md").read_text(encoding="utf-8"))
    roles = {}
    for f in sorted((skill / "roles").glob("0[1-6]-*.md")):
        roles[int(f.name[:2])] = norm(f.read_text(encoding="utf-8"))
    if sorted(roles) != [1, 2, 3, 4, 5, 6]:
        sys.exit(f"角色卡不完整：只找到席位 {sorted(roles)}")

    cfg = json.loads((run / "seats.json").read_text(encoding="utf-8"))
    rows, missing, cfg_errors = validate_seats_config(cfg)
    seats = [audit_seat(s, cfg["parent_id"], brief, roles, schema) for s in rows]
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
        residual_note = "任務中的額外文字（重派席位僅允許一行程序性修正提示，請人工確認未暗示立場）：\n" + "\n".join(
            f"- 席位 {s['seat']}{'（重派）' if any(c.get('seat') == s['seat'] and c.get('retry') for c in cfg['seats']) else ''}：{s['residual'][:150]}{'…（完整內容見 audit.json）' if len(s['residual']) > 150 else ''}"
            for s in seats if s["residual"])

    payload = {"seats": seats, "aggregate": agg, "config_errors": cfg_errors}
    if a.list_values:
        payload["reported_values"] = collect_values(seats)
    (run / "audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(to_markdown(seats, agg, residual_note))
    if a.list_values:
        print("\n" + values_markdown(payload["reported_values"]))
    if a.strict:
        failed = [x["seat"] for x in seats if x["verdict"] != "通過"]
        if failed or cfg_errors:
            sys.exit(f"strict：席位 {failed} 未通過；席位登錄錯誤 {len(cfg_errors)} 則")


if __name__ == "__main__":
    main()
