"""儀表板建置腳本與範本的測試（不跑瀏覽器，以 Python 檢查）。"""
import json
import re

from conftest import DASHBOARD, SKILL_DIR, make_run, run_script, write_dashboard_input

TEMPLATE = SKILL_DIR / "templates" / "dashboard.html"
DATA_RE = re.compile(r'<script type="application/json" id="council-data">\n(.*?)\n</script>', re.S)


def _seat(n, **extra):
    s = {"seat": n, "conversation_id": f"conv-{n}", "verdict": "通過", "isolation": [],
         "dispatch": [], "residual": None, "format_errors": [], "format_warnings": [],
         "warnings": [], "retried": False, "excluded_by_role_check": False,
         "tools_used": ["send_message"], "stance": "有條件推進",
         "scores": {d: 5 for d in ("生存安全", "上限潛力", "執行可行性", "長期穩定", "假設穩健度", "人因可持續性")},
         "chars": 10, "report": f"報告 {n}"}
    s.update(extra)
    return s


def _build(tmp_path, seats, synthesis=None, valid=None):
    run = make_run(tmp_path / "run")
    audit = {"seats": seats, "aggregate": {"valid_seats": valid if valid is not None else [s["seat"] for s in seats]}}
    (run["run_dir"] / "audit.json").write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    data = {"meta": {"title": "測試", "run_id": "t", "generated_at": "x", "dispatch_mode": "標準",
                     "audit_method": "腳本", "retry_count": 0},
            "synthesis": synthesis or {}}
    write_dashboard_input(run["run_dir"], data)
    cp = run_script(DASHBOARD, run)
    return run, cp, audit, data


def _embedded(run):
    html = (run["run_dir"] / "plan_preview.html").read_text(encoding="utf-8")
    return html, json.loads(DATA_RE.search(html).group(1))


def test_build_roundtrip_with_new_fields(tmp_path):
    seats = [_seat(1, retried=True), _seat(2, excluded_by_role_check=True, warnings=["離題"]),
             _seat(3), _seat(4), _seat(5), _seat(6)]
    run, cp, audit, data = _build(tmp_path, seats, {"absent_reasons": {"2": "離題"}}, valid=[1, 3, 4, 5, 6])
    assert cp.returncode == 0, cp.stderr
    html, emb = _embedded(run)
    assert emb["audit"]["seats"] == audit["seats"]
    assert emb["audit"]["aggregate"] == audit["aggregate"]
    assert emb["synthesis"] == data["synthesis"]
    assert emb["meta"]["is_sample"] is False


def test_build_tolerates_old_audit_without_new_fields(tmp_path):
    seats = [{k: v for k, v in _seat(n).items() if k not in ("retried", "excluded_by_role_check", "warnings")}
             for n in range(1, 7)]
    run, cp, _, _ = _build(tmp_path, seats)
    assert cp.returncode == 0, cp.stderr
    assert (run["run_dir"] / "plan_preview.html").is_file()


def test_sample_data_has_distinct_seats():
    html = TEMPLATE.read_text(encoding="utf-8")
    sample = json.loads(DATA_RE.search(html).group(1))
    nums = [s["seat"] for s in sample["audit"]["seats"]]
    assert len(nums) == len(set(nums)) == 6
    assert any(s.get("retried") for s in sample["audit"]["seats"])
    assert any(s.get("excluded_by_role_check") for s in sample["audit"]["seats"])


def test_template_reads_new_fields():
    html = TEMPLATE.read_text(encoding="utf-8")
    for token in ("retried", "excluded_by_role_check", "absent_reasons", "已重派", "離題排除"):
        assert token in html
    assert "count > 1" not in html


def test_script_tag_in_report_cannot_break_out(tmp_path):
    evil = "</script><script>alert(1)</script>&"
    run, cp, _, _ = _build(tmp_path, [_seat(n, report=evil if n == 1 else "ok") for n in range(1, 7)],
                           {"conflicts": [evil]})
    assert cp.returncode == 0, cp.stderr
    html, emb = _embedded(run)
    assert emb["audit"]["seats"][0]["report"] == evil
    assert emb["synthesis"]["conflicts"] == [evil]
    body = DATA_RE.search(html).group(1)
    assert "</script" not in body and "<" not in body
