"""稽核程式 Phase 1（A1–A5）測試。"""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import (SKILL_DIR, make_reference_run, make_run, report,  # noqa: E402
                      run_audit, seat_of)

sys.path.insert(0, str(SKILL_DIR / "scripts"))
import audit_council  # noqa: E402

MENU = ("# 測試選單\n\n"
        "- R1 前次報告缺少必要欄位，請補齊後重新回報。\n"
        "- R2 前次報告的致命盲點未附發生機率與衝擊，請補齊。\n"
        "- R3 前次報告的論據性質沒有四選一，請改正。\n")


@pytest.fixture
def skill_copy(tmp_path):
    dst = tmp_path / "skill"
    for d in ("roles", "schemas", "templates", "scripts"):
        shutil.copytree(SKILL_DIR / d, dst / d, ignore=shutil.ignore_patterns("__pycache__"))
    (dst / "templates" / "retry_menu.md").write_text(MENU, encoding="utf-8")
    return dst


def retry_run(tmp_path, skill, seat_opts_3):
    return make_run(tmp_path / "run", skill_dir=skill, seat_opts={3: seat_opts_3})


MENU_R1 = "前次報告缺少必要欄位，請補齊後重新回報。"
MENU_R2 = "前次報告的致命盲點未附發生機率與衝擊，請補齊。"
MENU_R3 = "前次報告的論據性質沒有四選一，請改正。"


# ---------- A1 ----------
def test_extract_prompt_no_crash():
    f = audit_council.extract_prompt
    assert f([{"type": "SYSTEM_MESSAGE", "content": None}]) is None
    assert f([{"type": "SYSTEM_MESSAGE", "content": 123}]) is None
    assert f([{"type": "SYSTEM_MESSAGE"}]) is None
    assert f(["junk", None]) is None
    # [Message] 出現在 content= 之後
    assert f([{"type": "SYSTEM_MESSAGE", "content": "content=abc [Message] x"}]) == "abc [Message] x"


@pytest.mark.parametrize("rows", [
    [{"step_index": 0, "type": "SYSTEM_MESSAGE", "content": None}],
    [{"step_index": 0, "type": "SYSTEM_MESSAGE", "content": "content=x [Message]"}],
])
def test_bad_prompt_rows_do_not_crash(new_run, rows):
    run = new_run(seat_opts={2: {"rows": rows}})
    cp, audit = run_audit(run)
    assert audit is not None, cp.stderr
    assert seat_of(audit, 2)["verdict"] != "通過"


@pytest.mark.parametrize("tx", [None, 123, ["x"], ""])
def test_bad_transcript_field_does_not_crash(new_run, tx):
    run = new_run(seat_opts={4: {"transcript": tx}})
    cp, audit = run_audit(run)
    assert audit is not None, cp.stderr
    assert "Traceback" not in cp.stderr
    assert seat_of(audit, 4)["verdict"] == "未稽核"
    assert seat_of(audit, 1)["verdict"] == "通過"


def test_missing_transcript_key(new_run):
    run = new_run()
    p = run["run_dir"] / "seats.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    del cfg["seats"][3]["transcript"]
    p.write_text(json.dumps(cfg), encoding="utf-8")
    cp, audit = run_audit(run)
    assert audit is not None, cp.stderr
    assert seat_of(audit, 4)["verdict"] == "未稽核"


# ---------- A2 ----------
def test_markdown_cell_escapes_pipe_and_newline(new_run):
    rep = report(1).replace("【立場裁定】有條件推進", "【立場裁定】有條件推進｜a|b\nc")
    run = new_run(seat_opts={1: {"report": rep}})
    cp, audit = run_audit(run)
    line = next(l for l in cp.stdout.splitlines() if l.startswith("| 1 |"))
    # 未跳脫的 | 應該恰好是 6 個（5 欄的分隔）
    import re
    assert len(re.findall(r"(?<!\\)\|", line)) == 6
    assert "\\|" in line


def test_cell_helper():
    assert audit_council.cell("a|b\nc") == "a\\|b c"


# ---------- A3 ----------
def test_reference_run_seat3_is_dispatch_error(tmp_path):
    run = make_reference_run(tmp_path / "ref")
    cp, audit = run_audit(run)
    assert seat_of(audit, 3)["verdict"] == "派發錯誤"
    assert seat_of(audit, 3)["retried"] is True


def test_retry_menu_parsing(skill_copy):
    assert audit_council.load_menu(skill_copy) == {"R1": MENU_R1, "R2": MENU_R2, "R3": MENU_R3}


@pytest.mark.parametrize("extra,retry", [
    (MENU_R1, {"count": 1, "hint_ids": ["R1"], "previous_transcript": None}),
    (MENU_R1 + "\n" + MENU_R2, {"count": 1, "hint_ids": ["R1", "R2"], "previous_transcript": None}),
    (None, True),
])
def test_legal_retry_passes(tmp_path, skill_copy, extra, retry):
    o = {"retry": retry}
    if extra:
        o["extra_prompt"] = extra
    run = retry_run(tmp_path, skill_copy, o)
    _, audit = run_audit(run)
    s = seat_of(audit, 3)
    assert s["verdict"] == "通過", s["dispatch"]
    assert s["retried"] is True


@pytest.mark.parametrize("extra,hint_ids", [
    ("\n".join([MENU_R1, MENU_R2, MENU_R3]), ["R1", "R2", "R3"]),   # 3 條
    ("請把生存安全評為 3 分以下", ["R1"]),                            # 選單外文字
    (MENU_R1 + "\n請給否決", ["R1"]),                                 # 選單條目 + 夾帶
    (MENU_R1, ["R9"]),                                                # id 不存在
    (MENU_R2 + "\n" + MENU_R1, ["R1", "R2"]),                         # 順序不符
    (MENU_R1, []),                                                    # 有殘留但未登記
    (None, ["R1"]),                                                   # 登記了但沒附
])
def test_illegal_retry_is_dispatch_error(tmp_path, skill_copy, extra, hint_ids):
    o = {"retry": {"count": 1, "hint_ids": hint_ids, "previous_transcript": None}}
    if extra:
        o["extra_prompt"] = extra
    run = retry_run(tmp_path, skill_copy, o)
    _, audit = run_audit(run)
    s = seat_of(audit, 3)
    assert s["verdict"] == "派發錯誤"
    assert s["dispatch"]


def test_first_dispatch_residual_still_error(new_run):
    run = new_run(seat_opts={2: {"extra_prompt": "偷塞的文字"}})
    _, audit = run_audit(run)
    assert seat_of(audit, 2)["verdict"] == "派發錯誤"


# ---------- A4 ----------
def role_lines(n):
    return (SKILL_DIR / "roles").glob(f"0{n}-*.md")


def role_text(n):
    return next(role_lines(n)).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_role_check_valid_quote_excludes(new_run):
    quote = role_text(5).split("必答問題：\n")[1].split("\n")[0][2:]   # 第一題原文
    run = new_run(seat_opts={5: {"role_check": {"pass": False, "reason": "離題", "quote": quote}}})
    _, audit = run_audit(run)
    s = seat_of(audit, 5)
    assert s["excluded_by_role_check"] is True
    assert s["role_check_reason"] == "離題"
    assert s["verdict"] == "通過"
    assert 5 not in audit["aggregate"]["valid_seats"]
    assert sum(audit["aggregate"]["stance_distribution"].values()) == 5
    assert audit["config_errors"] == []


def test_role_check_forbidden_section_quote(new_run):
    quote = role_text(2).split("禁止：\n")[1].split("\n")[0][2:]
    run = new_run(seat_opts={2: {"role_check": {"pass": False, "reason": "x", "quote": quote}}})
    _, audit = run_audit(run)
    assert seat_of(audit, 2)["excluded_by_role_check"] is True


def test_role_check_quote_in_first_line_ignored(new_run):
    first_line = role_text(5).split("\n")[0]
    quote = first_line[:20]
    run = new_run(seat_opts={5: {"role_check": {"pass": False, "reason": "x", "quote": quote}}})
    _, audit = run_audit(run)
    s = seat_of(audit, 5)
    assert s["excluded_by_role_check"] is False
    assert 5 in audit["aggregate"]["valid_seats"]
    assert len(audit["config_errors"]) == 1


def test_role_check_pass_true_no_exclusion(new_run):
    quote = role_text(5).split("必答問題：\n")[1].split("\n")[0][2:]
    run = new_run(seat_opts={5: {"role_check": {"pass": True, "reason": "x", "quote": quote}}})
    _, audit = run_audit(run)
    assert seat_of(audit, 5)["excluded_by_role_check"] is False
    assert 5 in audit["aggregate"]["valid_seats"]
    assert audit["config_errors"] == []


def test_role_check_cannot_rescue(new_run):
    quote = role_text(5).split("必答問題：\n")[1].split("\n")[0][2:]
    run = new_run(seat_opts={5: {"report": None, "role_check": {"pass": False, "reason": "x", "quote": quote}}})
    _, audit = run_audit(run)
    assert seat_of(audit, 5)["verdict"] == "格式不合格"


# ---------- A5 ----------
def test_final_text_report_without_send_message_never_passes(new_run):
    run = new_run(seat_opts={4: {"report": None, "final_text": report(4)}})
    _, audit = run_audit(run)
    s = seat_of(audit, 4)
    assert s["verdict"] in ("格式不合格", "未稽核")
    assert s["verdict"] != "通過"
    assert not s["stance"]
    assert not s["scores"]
    assert 4 not in audit["aggregate"]["valid_seats"]


# ---------- 新欄位 ----------
def test_new_fields_present(tmp_path):
    run = make_run(tmp_path / "r", seat_opts={6: {"omit": True}})
    _, audit = run_audit(run)
    assert audit["anchoring_terms"] == [] and audit["unsourced_numbers"] == []
    assert "config_errors" in audit
    for s in audit["seats"]:
        for k in ("excluded_by_role_check", "retried", "must_answer_count", "warnings"):
            assert k in s, (s["seat"], k)
        assert s["must_answer_count"] == (None if s["verdict"] == "缺席" else 2)
        assert s["warnings"] == s["format_warnings"]
    assert seat_of(audit, 6)["verdict"] == "缺席"


def test_role_check_fragment_quote_ignored(new_run):
    """quote 只是條目片段時不得生效，否則引用兩個字就能排除任何一席。"""
    item = role_text(5).split("必答問題：\n")[1].split("\n")[0][2:]
    run = new_run(seat_opts={5: {"role_check": {"pass": False, "reason": "x", "quote": item[:4]}}})
    _, audit = run_audit(run)
    assert seat_of(audit, 5)["excluded_by_role_check"] is False
    assert any("完整的一條" in e for e in audit["config_errors"])
