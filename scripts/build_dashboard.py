#!/usr/bin/env python3
"""
Agentic Council 儀表板產生腳本

用法：
    python build_dashboard.py --skill-dir <skill 資料夾> --run-dir <本次執行資料夾>

run-dir 內必須有：
    audit.json            稽核腳本的輸出（seats 與 aggregate 原封不動帶入頁面）
    dashboard_input.json  主 Agent 撰寫的內容，格式：
        {
          "meta": {"title": ..., "run_id": ..., "generated_at": ...,
                   "dispatch_mode": "標準"|"降級", "audit_method": "腳本"|"人工",
                   "retry_count": 0},
          "synthesis": { ...欄位見 templates/dashboard.md... }
        }

輸出：
    run-dir/plan_preview.html

資料中的 < > & 一律轉成 \\u003c 之類的 JSON 跳脫，報告內容即使含有 </script>
也無法跳出資料區塊。只使用 Python 標準函式庫。
"""

import argparse
import json
import sys
from pathlib import Path

BEGIN = "<!-- COUNCIL-DATA:BEGIN -->"
END = "<!-- COUNCIL-DATA:END -->"


def safe_json(obj):
    # 這三個字元在 JSON 中只會出現在字串內，跳脫後 JSON.parse 會還原成原字元。
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def load(path):
    if not path.is_file():
        sys.exit(f"找不到 {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"{path} 不是合法的 JSON：{e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--run-dir", required=True)
    a = ap.parse_args()

    skill, run = Path(a.skill_dir), Path(a.run_dir)
    audit = load(run / "audit.json")
    inp = load(run / "dashboard_input.json")

    for key in ("seats", "aggregate"):
        if key not in audit:
            sys.exit(f"audit.json 缺少 {key}")
    for key in ("meta", "synthesis"):
        if not isinstance(inp.get(key), dict):
            sys.exit(f"dashboard_input.json 缺少 {key} 物件")

    data = {
        "meta": {**inp["meta"], "is_sample": False},
        "audit": {"seats": audit["seats"], "aggregate": audit["aggregate"]},
        "synthesis": inp["synthesis"],
    }

    template = (skill / "templates" / "dashboard.html").read_text(encoding="utf-8")
    if template.count(BEGIN) != 1 or template.count(END) != 1:
        sys.exit("dashboard.html 的 COUNCIL-DATA 標記不完整，請勿修改範本")
    head, rest = template.split(BEGIN)
    _, tail = rest.split(END)
    block = ('\n<script type="application/json" id="council-data">\n'
             + safe_json(data) + "\n</script>\n")

    out = run / "plan_preview.html"
    out.write_text(head + BEGIN + block + END + tail, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
