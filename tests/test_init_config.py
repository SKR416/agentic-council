"""環境探測與模型編制初始化（init_council.py 與 audit_council.py 整合）測試。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import AUDIT, SKILL_DIR, make_run, run_audit, seat_of  # noqa: E402

sys.path.insert(0, str(SKILL_DIR / "scripts"))
import audit_council  # noqa: E402
import init_council  # noqa: E402
from transcripts import TranscriptFormatError, get_adapter  # noqa: E402

INIT = SKILL_DIR / "scripts" / "init_council.py"


def run_init(workspace, *args):
    return subprocess.run(
        [sys.executable, str(INIT), "--skill-dir", str(SKILL_DIR),
         "--workspace", str(workspace), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8",
    )


def test_load_menu_supports_all_prefixes():
    r_menu = audit_council.load_menu(SKILL_DIR, "R")
    p_menu = audit_council.load_menu(SKILL_DIR, "P")
    m_menu = audit_council.load_menu(SKILL_DIR, "M")
    assert "R1" in r_menu
    assert "P1" in p_menu and "P9" in p_menu
    assert set(m_menu) == {"M1", "M2", "M3", "M4", "M5"}


def test_probe_when_no_config(tmp_path):
    cp = run_init(tmp_path, "--probe")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert data["exists"] is False
    assert data["valid"] is False
    assert "agy" in data["detected"]["platforms"]
    assert "M1" in data["menu"]


@pytest.mark.parametrize("preset,expected_model", [
    ("M1", "inherit"),
    ("M2", "pro"),
    ("M3", "flash"),
    ("M4", "flash_lite"),
])
def test_write_preset_and_probe_valid(tmp_path, preset, expected_model):
    cp = run_init(tmp_path, "--preset", preset)
    assert cp.returncode == 0, cp.stderr
    cfg_p = tmp_path / ".council" / "config.json"
    assert cfg_p.is_file()
    cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
    assert cfg["model_preset"] == preset
    assert cfg["seat_models"] == {str(n): expected_model for n in range(1, 7)}

    cp_probe = run_init(tmp_path, "--probe")
    assert cp_probe.returncode == 0
    st = json.loads(cp_probe.stdout)
    assert st["exists"] is True and st["valid"] is True and st["errors"] == []


def test_custom_symmetric_models_m5(tmp_path):
    cp = run_init(
        tmp_path, "--preset", "M5",
        "--seat-models", "1=pro,2=pro,3=flash,4=flash,5=claude-opus,6=gpt-5",
    )
    assert cp.returncode == 0, cp.stderr
    cfg = json.loads((tmp_path / ".council" / "config.json").read_text(encoding="utf-8"))
    assert cfg["seat_models"]["1"] == "pro"
    assert cfg["seat_models"]["3"] == "flash"
    assert cfg["seat_models"]["5"] == "claude-opus"


@pytest.mark.parametrize("bad_models", [
    "1=flash_lite,2=pro,3=pro,4=pro,5=pro,6=pro",     # 1 vs 2 算力不對稱
    "1=pro,2=pro,3=pro,4=flash,5=pro,6=pro",          # 3 vs 4 算力不對稱
    "1=pro,2=pro,3=pro,4=pro,5=inherit,6=pro",        # 5 vs 6 一席 inherit 一席固定
    "1=pro,2=pro,3=pro,4=pro,5=pro",                  # 缺席位 6
])
def test_asymmetric_or_incomplete_models_rejected(tmp_path, bad_models):
    cp = run_init(tmp_path, "--preset", "M5", "--seat-models", bad_models)
    assert cp.returncode != 0
    assert "錯誤" in cp.stderr


def test_audit_auto_loads_config_brain_dir_and_models(tmp_path):
    # 建立標準目錄結構 <workspace>/.council/runs/run1
    ws = tmp_path / "ws"
    run_dir = ws / ".council" / "runs" / "20261021-220000"
    run = make_run(run_dir)
    # 寫入 .council/config.json，把 brain_dir 設進去，之後呼叫 audit_council 不傳 --brain-dir
    cp_init = run_init(ws, "--preset", "M2", "--brain-dir", str(run["brain_dir"]))
    assert cp_init.returncode == 0, cp_init.stderr

    cp = subprocess.run(
        [sys.executable, str(AUDIT), "--skill-dir", str(SKILL_DIR), "--run-dir", str(run_dir)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert cp.returncode == 0, cp.stderr
    audit = json.loads((run_dir / "audit.json").read_text(encoding="utf-8"))
    assert audit["config_errors"] == []
    assert all(s["verdict"] == "通過" for s in audit["seats"])
    assert all(s["model"] == "pro" for s in audit["seats"])
    summ = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert "［模型：pro］" in summ


def test_audit_detects_seat_model_mismatch_with_config(tmp_path):
    ws = tmp_path / "ws"
    run_dir = ws / ".council" / "runs" / "20261021-220000"
    run = make_run(run_dir)
    run_init(ws, "--preset", "M2", "--brain-dir", str(run["brain_dir"]))
    # 把席位 1 在 seats.json 的 model 改成 flash（與 config.json 的 pro 不符）
    seats_p = run_dir / "seats.json"
    seats_cfg = json.loads(seats_p.read_text(encoding="utf-8"))
    seats_cfg["seats"][0]["model"] = "flash"
    seats_p.write_text(json.dumps(seats_cfg), encoding="utf-8")

    _, audit = run_audit(run)
    assert seat_of(audit, 1)["verdict"] == "派發錯誤"
    assert any("config.json" in d for d in seat_of(audit, 1)["dispatch"])
    assert seat_of(audit, 2)["verdict"] == "通過"


def test_audit_detects_asymmetric_models_in_seats_json(new_run):
    run = new_run()
    seats_p = run["run_dir"] / "seats.json"
    seats_cfg = json.loads(seats_p.read_text(encoding="utf-8"))
    seats_cfg["seats"][0]["model"] = "flash_lite"
    seats_cfg["seats"][1]["model"] = "pro"
    seats_p.write_text(json.dumps(seats_cfg), encoding="utf-8")

    _, audit = run_audit(run)
    assert any("算力不對稱" in e for e in audit["config_errors"])


def test_get_adapter_unknown_platform_raises():
    with pytest.raises(TranscriptFormatError, match="找不到平台"):
        get_adapter("nonexistent_platform_xyz")


# ---------- 問題 3：數值同義單位與中文數字互通 ----------
@pytest.mark.parametrize("cn,expected", [
    ("三", "3"),
    ("兩", "2"),
    ("十", "10"),
    ("十二", "12"),
    ("三十", "30"),
    ("三十五", "35"),
    ("一百二十", "120"),
    ("三千五百", "3500"),
])
def test_cn_to_arabic(cn, expected):
    assert audit_council.cn_to_arabic(cn) == expected


def test_check_plan_synonyms_and_chinese_numerals():
    brief = "工期預計三週，緩衝 7 日，預算 100 萬元，配置 3 名工程師。"
    plan = "第一階段 3 週完成，預留七天緩衝，總預算 100 萬，指派三個人。"
    seats = [{"seat": 1, "verdict": "通過", "excluded_by_role_check": False, "report": "指派 3 人"}]
    unsourced, sourced = audit_council.check_plan_numbers(plan, brief, seats)
    assert unsourced == []
    assert {s["value"] for s in sourced} >= {"3 週", "七天", "100 萬"}


# ---------- 問題 4：role_check_quote 寫入 audit.json、report_full.md 與 dashboard ----------
def test_role_check_quote_in_audit_and_report_full(new_run):
    from conftest import load_roles
    quote = load_roles()[4].split("禁止：\n")[1].split("\n")[0][2:]
    run = new_run(seat_opts={4: {"role_check": {"pass": False, "reason": "僅給空泛結論", "quote": quote}}})
    _, audit = run_audit(run)
    s4 = seat_of(audit, 4)
    assert s4["excluded_by_role_check"] is True
    assert s4["role_check_reason"] == "僅給空泛結論"
    assert s4["role_check_quote"] == quote
    full = (run["run_dir"] / "report_full.md").read_text(encoding="utf-8")
    assert f"引用角色卡條款：「{quote}」" in full
    assert "原因：僅給空泛結論" in full


def test_dashboard_template_renders_role_check_quote():
    html = (SKILL_DIR / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "role_check_quote" in html
    assert "引用角色卡條款" in html


# ---------- 問題 5：stance_record.md 立場剝除摘要 ----------
def test_stance_record_summary_in_audit_and_summary_md(new_run):
    run = new_run()
    stance_md = (
        "# 立場剝除紀錄\n\n"
        "> 本紀錄僅供主 Agent 仲裁與使用者檢查使用，嚴禁傳給任何子代理。\n\n"
        "## 剝除的原句\n"
        "- 方案 A 明顯比較好，完全沒風險\n"
        "- 方案 B 太保守了\n\n"
        "## 使用者既有傾向摘要\n"
        "明顯傾向採用方案 A。\n"
    )
    (run["run_dir"] / "stance_record.md").write_text(stance_md, encoding="utf-8")
    _, audit = run_audit(run)
    assert audit["stance_summary"] == {
        "stripped_count": 2,
        "tendency_summary": "明顯傾向採用方案 A。",
    }
    summ = (run["run_dir"] / "summary.md").read_text(encoding="utf-8")
    assert "## 立場剝除摘要" in summ
    assert "剝除原句條數：2 條" in summ
    assert "使用者既有傾向摘要：明顯傾向採用方案 A。" in summ


# ---------- 問題 6-c：錨定詞排除 preset_menu.md 與通用模板語句 ----------
def test_anchoring_excludes_preset_menu_phrases(new_run):
    from conftest import report
    brief = "# Baseline Brief\n\n## 決策邊界\n- 衡量權重：以穩定不中斷為主要考量（使用者採用預設）\n- 方案：導入零信任閘道器\n"
    opts = {
        n: {"report": report(n, extra="，以穩定不中斷為主要考量，使用者採用預設，導入零信任閘道器")}
        for n in (1, 2)
    }
    _, audit = run_audit(new_run(brief=brief, seat_opts=opts))
    terms = [t["term"] for t in audit["anchoring_terms"]]
    assert not any("穩定不中斷" in t for t in terms)
    assert not any("使用者採用預設" in t for t in terms)
    assert any("零信任閘道器" in t for t in terms)
