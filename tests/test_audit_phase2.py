"""稽核程式 Phase 2（A6–A15）測試。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import (AUDIT, SKILL_DIR, make_reference_run, make_run, report,  # noqa: E402
                      run_audit, run_script, seat_of, transcript_path)

sys.path.insert(0, str(SKILL_DIR / "scripts"))
import audit_council  # noqa: E402
from transcripts import agy  # noqa: E402


# ---------- A6 ----------
def test_adapter_functions_exist():
    assert callable(agy.load) and callable(agy.extract_prompt) and callable(agy.extract_tool_calls)
    assert audit_council.tx_adapter is agy


def test_adapter_rejects_non_jsonl(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("this is not json\n", encoding="utf-8")
    with pytest.raises(agy.TranscriptFormatError, match="不是 JSONL"):
        agy.load(p)


def test_adapter_rejects_unknown_types(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"type": "FOO"}\n{"a": 1}\n', encoding="utf-8")
    with pytest.raises(agy.TranscriptFormatError, match="無法辨識"):
        agy.load(p)


def test_adapter_accepts_valid(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"type": "SYSTEM_MESSAGE", "content": "content=x"}\n', encoding="utf-8")
    assert len(agy.load(p)) == 1


def test_bad_format_transcript_is_unaudited_with_message(new_run):
    run = new_run()
    tx = transcript_path(run["brain_dir"], "conv-2")
    tx.write_text("not json at all", encoding="utf-8")
    cp, audit = run_audit(run)
    s = seat_of(audit, 2)
    assert s["verdict"] == "未稽核"
    assert any("JSONL" in m for m in s["isolation"])
    assert seat_of(audit, 1)["verdict"] == "通過"


def test_script_runs_from_other_cwd(new_run, tmp_path):
    run = new_run()
    cp = subprocess.run([sys.executable, str(AUDIT), "--skill-dir", str(run["skill_dir"]),
                         "--run-dir", str(run["run_dir"]), "--brain-dir", str(run["brain_dir"])],
                        capture_output=True, text=True, encoding="utf-8", cwd=str(tmp_path))
    assert cp.returncode == 0, cp.stderr
    assert (run["run_dir"] / "audit.json").is_file()


# ---------- A7 ----------
def test_reference_run_seat6_unaudited(tmp_path):
    run = make_reference_run(tmp_path / "ref")
    cp, audit = run_audit(run)
    assert seat_of(audit, 6)["verdict"] == "未稽核"
    assert seat_of(audit, 3)["verdict"] == "派發錯誤"
    assert seat_of(audit, 1)["verdict"] == "通過"


def test_path_outside_brain_not_read(new_run, tmp_path):
    run = new_run()
    # 把席位 2 的 transcript 改指到 brain 之外的複本
    src = transcript_path(run["brain_dir"], "conv-2")
    other = tmp_path / "elsewhere" / "transcript_full.jsonl"
    other.parent.mkdir()
    other.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    p = run["run_dir"] / "seats.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg["seats"][1]["transcript"] = str(other)
    p.write_text(json.dumps(cfg), encoding="utf-8")
    _, audit = run_audit(run)
    s = seat_of(audit, 2)
    assert s["verdict"] == "未稽核"
    assert s["tools_used"] == [] and s["report"] == ""     # 沒有讀取
    assert any("路徑不符" in m for m in s["isolation"])


def test_path_wrong_brain_unaudited(new_run, tmp_path):
    run = new_run()
    _, audit = run_audit(run, "--brain-dir", tmp_path / "other-brain")
    assert all(s["verdict"] == "未稽核" for s in audit["seats"])


def test_multiple_brain_dirs(new_run, tmp_path):
    run = new_run()
    _, audit = run_audit(run, "--brain-dir", tmp_path / "other-brain", "--brain-dir", run["brain_dir"])
    assert seat_of(audit, 1)["verdict"] == "通過"


def test_path_case_insensitive_on_windows(new_run):
    if sys.platform != "win32":
        pytest.skip("only windows")
    run = new_run()
    p = run["run_dir"] / "seats.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg["seats"][0]["transcript"] = cfg["seats"][0]["transcript"].upper()
    p.write_text(json.dumps(cfg), encoding="utf-8")
    _, audit = run_audit(run)
    assert seat_of(audit, 1)["verdict"] == "通過"


def test_conversation_id_with_separator_rejected():
    assert audit_council.check_transcript_path(Path("x"), "a/b", ["."]) is not None
    assert audit_council.check_transcript_path(Path("x"), "a\b", ["."]) is not None


# ---------- A8 ----------
def test_aggregate_only_counts_passed():
    def row(n, verdict, excluded=False):
        return {"seat": n, "verdict": verdict, "excluded_by_role_check": excluded,
                "stance": "支持推進", "scores": {d: 5 for d in audit_council.DIMENSIONS}, "report": ""}
    agg = audit_council.aggregate([row(1, "通過"), row(2, "未稽核"), row(3, "格式不合格"),
                                   row(4, "通過", True), row(5, "通過")])
    assert agg["valid_seats"] == [1, 5]


def test_unaudited_seat_not_in_aggregate_e2e(new_run):
    run = new_run(seat_opts={2: {"tool_calls": [{"name": "send_message", "args": {"Message": "x"}}]}})  # 收件者不明
    _, audit = run_audit(run)
    assert seat_of(audit, 2)["verdict"] == "未稽核"
    assert 2 not in audit["aggregate"]["valid_seats"]
    assert 1 in audit["aggregate"]["valid_seats"]


def test_collect_values_excludes_role_check_seats():
    rep = "最強 預算 500 萬"
    rows = [{"seat": 1, "verdict": "通過", "report": rep, "excluded_by_role_check": False},
            {"seat": 2, "verdict": "通過", "report": rep, "excluded_by_role_check": True}]
    assert list(audit_council.collect_values(rows)) == [1]


# ---------- A9 ----------
def val(rep, seat=1, tools=None):
    from conftest import load_roles
    role = audit_council.role_name(load_roles()[seat])
    return audit_council.validate_report(rep, 5000, role, tools)


def test_baseline_report_valid():
    errs, warns, res = val(report(1))
    assert errs == [] and warns == []
    assert res["must_answer_count"] == 2 and res["stance"] == "有條件推進"


def test_score_only_from_score_field():
    rep = report(1).replace("生存安全 5/10｜", "").replace(
        "【最強支持理由】測試", "【最強支持理由】測試 生存安全 9/10")
    errs, _, res = val(rep)
    assert any("缺少評分：生存安全" in e for e in errs)
    assert "生存安全" not in res["scores"]


def test_bold_and_colon_accepted():
    rep = (report(1).replace("【立場裁定】有條件推進", "**【立場裁定】**：**有條件推進**")
           .replace("生存安全 5/10", "生存安全：**5/10**"))
    errs, _, res = val(rep)
    assert errs == [], errs
    assert res["scores"]["生存安全"] == 5 and res["stance"] == "有條件推進"


def test_stance_outside_field_not_used():
    rep = report(1).replace("【立場裁定】有條件推進", "【立場裁定】\n（空）")
    errs, _, res = val(rep)
    assert res["stance"] is None


def test_confidence_requires_explanation():
    bad = report(1).replace("【信心程度】中 —— 測試", "【信心程度】中")
    good = report(1)
    assert any("信心程度" in e for e in val(bad)[0])
    assert not any("信心程度" in e for e in val(good)[0])


def test_confidence_not_level():
    bad = report(1).replace("【信心程度】中 —— 測試", "【信心程度】很高 —— 測試")
    assert any("信心程度不是" in e for e in val(bad)[0])


def test_field_order_warning_only():
    r = report(1)
    a, b = "【最強支持理由】測試\n", "【致命盲點】"
    # 把【防禦性底線】移到【席位】之後
    lines = r.split("\n")
    tail = lines.pop()
    lines.insert(1, tail)
    errs, warns, _ = val("\n".join(lines))
    assert errs == []
    assert any("順序" in w for w in warns)
    assert not any("順序" in w for w in val(r)[1])


# ---------- A11 ----------
EXT = report(1).replace("論據性質：[推測]", "論據性質：[外部來源] https://example.com/a")


def test_external_source_needs_tool_call():
    assert any("search_web" in e for e in val(EXT, tools=["send_message"])[0])
    assert not any("search_web" in e for e in val(EXT, tools=["search_web", "send_message"])[0])
    assert not any("search_web" in e for e in val(EXT, tools=["read_url_content"])[0])


def test_external_source_e2e(new_run):
    run = new_run(seat_opts={
        1: {"report": EXT},
        2: {"report": report(2).replace("論據性質：[推測]", "論據性質：[外部來源] https://e.com/x"),
            "tool_calls": [{"name": "search_web", "args": {"query": "x"}}]}})
    _, audit = run_audit(run)
    assert seat_of(audit, 1)["verdict"] == "格式不合格"
    assert seat_of(audit, 2)["verdict"] == "通過"


# ---------- A13 ----------
def test_must_answer_min():
    one = report(1, must=("Q1：只有一題",))
    dup = report(1, must=("Q1：a", "Q1：b"))
    none = report(1, must=None)
    two = report(1, must=("Q1：a", "Q3：b"))
    assert any("必答回應" in e for e in val(one)[0])
    assert any("必答回應" in e for e in val(dup)[0])
    assert any("缺少欄位 【必答回應】" in e for e in val(none)[0])
    assert not any("必答回應" in e for e in val(two)[0])
    assert val(dup)[2]["must_answer_count"] == 1


def test_must_answer_e2e(new_run):
    run = new_run(seat_opts={1: {"report": report(1, must=("Q1：只有一題",))},
                             2: {"report": report(2, must=("Q1：a", "Q2：b", "Q3：c"))}})
    _, audit = run_audit(run)
    assert seat_of(audit, 1)["verdict"] == "格式不合格"
    assert seat_of(audit, 1)["must_answer_count"] == 1
    assert seat_of(audit, 2)["must_answer_count"] == 3


# ---------- A14 ----------
def test_char_limit_option(new_run):
    run = new_run()
    _, audit = run_audit(run, "--char-limit", 100)
    s = seat_of(audit, 1)
    assert s["verdict"] == "通過"                      # 超過只警告
    assert any("超過 100 字" in w for w in s["warnings"])
    assert any("超過 125 字" in w for w in seat_of(audit, 5)["warnings"])   # 100*1500/1200
    _, audit = run_audit(run)
    assert seat_of(audit, 1)["warnings"] == []


def test_char_limit_seat5_option(new_run):
    run = new_run()
    _, audit = run_audit(run, "--char-limit", 100, "--char-limit-seat5", 140)
    assert any("超過 140 字" in w for w in seat_of(audit, 5)["warnings"])


def test_build_char_limits():
    assert audit_council.char_limit(5) == 1500 and audit_council.char_limit(2) == 1200


# ---------- A10 ----------
def vals(text):
    return [n["value"] for n in audit_council.extract_numbers(text)]


def test_extract_ignores_urls_and_dates():
    t = "見 https://example.com/a/2026/10/19/post-123 與 2026-10-19、2026/10/19、10月19日、2026年10月19日。"
    assert vals(t) == []


def test_extract_new_units_and_thousands():
    assert vals("共 12 個、5 件、300 台、40 家、8 位、7 項、20 筆、100 張、30 份、9 套、15 輛、60 間、25 場、10 頁、50 行、800 字") == [
        "12 個", "5 件", "300 台", "40 家", "8 位", "7 項", "20 筆", "100 張", "30 份", "9 套", "15 輛",
        "60 間", "25 場", "10 頁", "50 行", "800 字"]
    assert vals("預算 1,000,000 元，另 3.5 萬、NT$1,200") == ["1,000,000 元", "3.5 萬", "NT$1,200"]


def test_extract_range_and_labels_not_negative_or_numeric():
    assert vals("3-5 天") == ["5 天"]          # 單獨的 3 被當成條列單一數字略過
    assert vals("見 Q12 與 R20 以及 v2.55") == []


def test_extract_key_normalizes():
    a = audit_council.extract_numbers("1,000 元")[0]["key"]
    b = audit_council.extract_numbers("1000元")[0]["key"]
    c = audit_council.extract_numbers("1000.0 元")[0]["key"]
    assert a == b == c
    assert audit_council.extract_numbers("1000 美元")[0]["key"] != a


def test_check_plan_numbers_unit():
    seats = [{"seat": 1, "verdict": "通過", "excluded_by_role_check": False, "report": "市場約 250 萬"},
             {"seat": 2, "verdict": "未稽核", "excluded_by_role_check": False, "report": "成本 777 萬"},
             {"seat": 3, "verdict": "通過", "excluded_by_role_check": True, "report": "目標 888 萬"}]
    un, so = audit_council.check_plan_numbers("預算 100萬；市場 250 萬；成本 777 萬；目標 888 萬；上線 3 週",
                                              "預算 100 萬", seats)
    assert [u["value"] for u in un] == ["777 萬", "888 萬", "3 週"]
    assert {u["value"] for u in so} == {"100萬", "250 萬"}
    assert all(u["context"] for u in un + so)


def test_check_plan_cli(new_run):
    run = new_run(brief="# 簡報\n\n預算 100 萬元，期限 30 天\n",
                  seat_opts={1: {"report": report(1, extra="估計市場 1,200 件")}})
    _, audit = run_audit(run)
    # 沒有 plan.md：明確報錯
    cp, _ = run_audit(run, "--check-plan")
    assert cp.returncode != 0 and "plan.md" in cp.stderr
    plan = run["run_dir"] / "plan.md"
    plan.write_text("## 方案\n\n預算 100萬元，期限 30 天，市場 1200 件。\n", encoding="utf-8")
    cp, audit = run_audit(run, "--check-plan")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert audit["unsourced_numbers"] == []
    assert "預算" in cp.stdout                                  # 找得到的也附前後文
    plan.write_text("## 方案\n\n預算 100 萬元，另追加 999 萬 的備援。\n", encoding="utf-8")
    cp, audit = run_audit(run, "--check-plan")
    assert cp.returncode != 0
    assert [u["value"] for u in audit["unsourced_numbers"]] == ["999 萬"]
    assert "追加" in audit["unsourced_numbers"][0]["context"]
    assert "999 萬" in cp.stdout


# ---------- A12 ----------
ABRIEF = "# 簡報\n\n我們計畫導入供應鏈協同平台，並整合既有的訂單系統；部署在 Kubernetes 上。\n"


def test_anchoring_terms_e2e(new_run):
    run = new_run(brief=ABRIEF, seat_opts={
        1: {"report": report(1, extra="導入供應鏈協同平台 與 Kubernetes")},
        2: {"report": report(2, extra="供應鏈協同平台 風險")},
        3: {"report": report(3, extra="訂單系統 單點")},
        4: {"report": report(4, extra="供應鏈協同平台 kubernetes")}})
    cp, audit = run_audit(run)
    terms = {t["term"]: t["seats"] for t in audit["anchoring_terms"]}
    assert terms["供應鏈協同平台"] == [1, 2, 4]
    assert terms["kubernetes"] == [1, 4]
    assert "訂單系統" not in terms                    # 只有 1 席
    assert "供應鏈協同" not in terms                  # 被同席位組的較長詞包含
    assert "供應鏈協同平台" in cp.stdout


def test_anchoring_excludes_unpassed_and_role_check(new_run):
    from conftest import load_roles
    quote = load_roles()[3].split("必答問題：\n")[1].split("\n")[0][2:]
    run = new_run(brief=ABRIEF, seat_opts={
        1: {"report": report(1, extra="供應鏈協同平台")},
        2: {"report": report(2, extra="供應鏈協同平台"), "extra_prompt": "偷塞"},        # 派發錯誤
        3: {"report": report(3, extra="供應鏈協同平台"),
            "role_check": {"pass": False, "reason": "x", "quote": quote}}})             # 被排除
    _, audit = run_audit(run)
    assert audit["anchoring_terms"] == []              # 只剩席位 1 有效
    run = new_run(brief=ABRIEF, seat_opts={
        1: {"report": report(1, extra="供應鏈協同平台")},
        2: {"report": report(2, extra="供應鏈協同平台"), "extra_prompt": "偷塞"},
        3: {"report": report(3, extra="供應鏈協同平台")}})
    _, audit = run_audit(run)
    assert audit["anchoring_terms"] == [{"term": "供應鏈協同平台", "seats": [1, 3]}]


def test_anchoring_no_fixed_vocab_and_cap():
    brief = "六維評分 生存安全 致命盲點 防禦性底線 " + " ".join(f"獨特詞彙甲{chr(0x4e00 + i)}乙丙丁" for i in range(40))
    rep = brief
    seats = [{"seat": n, "verdict": "通過", "excluded_by_role_check": False, "report": rep} for n in (1, 2)]
    fixed = "六維評分 生存安全 致命盲點 防禦性底線"
    terms = audit_council.compute_anchoring(brief, seats, fixed)
    assert len(terms) <= 20
    assert not any(t["term"] in fixed for t in terms)
    assert terms == audit_council.compute_anchoring(brief, seats, fixed)    # 確定性
    assert audit_council.compute_anchoring(brief, seats[:1], fixed) == []


# ---------- A15 ----------
def test_summary_and_report_full(tmp_path):
    run = make_reference_run(tmp_path / "ref")
    rep2 = report(2, extra="獨一無二的支持句")
    run = make_run(tmp_path / "r", seat_opts={
        2: {"report": rep2}, 3: {"extra_prompt": "偷塞的文字"}, 6: {"conversation_id": "other"}})
    cp, audit = run_audit(run)
    summ = (run["run_dir"] / "summary.md").read_text(encoding="utf-8")
    full = (run["run_dir"] / "report_full.md").read_text(encoding="utf-8")
    # 原句
    assert "測試獨一無二的支持句" in summ
    assert "- 致命盲點 1 場景：測試情境" in summ
    assert "- 立場：有條件推進" in summ
    # 未通過席位標明狀態
    assert "席位 3" in summ and "派發錯誤（不計票）" in summ
    assert "席位 6" in summ and "未稽核（不計票）" in summ
    # 稽核表在末尾
    assert summ.rstrip().splitlines()[-1].startswith("|") or "六維評分總表" in summ
    assert summ.index("## 稽核表") > summ.index("席位 6")
    # 完整報告：六席原文 + 稽核表
    for n in range(1, 7):
        assert f"席位 {n}" in full
    assert rep2 in full
    assert "機械式稽核結果" in full
    assert "機械式稽核結果" in cp.stdout


def test_summary_marks_role_check_exclusion(new_run):
    from conftest import load_roles
    quote = load_roles()[4].split("禁止：\n")[1].split("\n")[0][2:]
    run = new_run(seat_opts={4: {"role_check": {"pass": False, "reason": "x", "quote": quote}}})
    run_audit(run)
    summ = (run["run_dir"] / "summary.md").read_text(encoding="utf-8")
    assert "離題排除（不計票）" in summ
