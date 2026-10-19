"""fixture 產生器本身的冒煙測試：乾淨的 6 席執行資料夾必須全部通過。"""
from conftest import run_audit


def test_clean_run_all_pass(new_run):
    cp, audit = run_audit(new_run(), "--strict")
    assert cp.returncode == 0, cp.stderr + cp.stdout
    assert [s["verdict"] for s in audit["seats"]] == ["通過"] * 6
