#!/usr/bin/env python3
"""
Agentic Council 環境探測與模型編制初始化腳本

用法：
    # 1. 探測環境與目前設定狀態（步驟 -1 讀取用）
    python init_council.py --skill-dir <skill 資料夾> --workspace <工作區根目錄> --probe

    # 2. 以預設套餐寫入 .council/config.json（M1=inherit, M2=pro, M3=flash, M4=flash_lite）
    python init_council.py --skill-dir <skill 資料夾> --workspace <工作區根目錄> --preset M2

    # 3. 自訂各席模型（M5，須符合對立席位算力對稱規則）
    python init_council.py --skill-dir <skill 資料夾> --workspace <工作區根目錄> --preset M5 \
        --seat-models "1=pro,2=pro,3=flash,4=flash,5=pro,6=pro"

只使用 Python 標準函式庫。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

CONFIG_VERSION = 1
OPPOSING_PAIRS = ((1, 2), (3, 4), (5, 6))
PRESET_TO_MODEL = {
    "M1": "inherit",
    "M2": "pro",
    "M3": "flash",
    "M4": "flash_lite",
}
# 同系列模型算力等級表：用於檢查對立席位是否出現算力不對稱（例如 1=flash_lite vs 2=pro）
KNOWN_MODEL_TIERS = {
    "pro": 3,
    "flash": 2,
    "flash_lite": 1,
}
CANDIDATE_BRAIN_DIRS = {
    "agy": (
        "~/.gemini/antigravity-cli/brain",
        "~/.gemini/antigravity/brain",
    ),
}


def available_platforms(skill_dir):
    tx_dir = Path(skill_dir) / "scripts" / "transcripts"
    if not tx_dir.is_dir():
        return ["agy"]
    out = [
        f.stem
        for f in sorted(tx_dir.glob("*.py"))
        if f.stem != "__init__" and not f.stem.startswith("_")
    ]
    return out or ["agy"]


def detect_brain_dirs(platform="agy"):
    candidates = CANDIDATE_BRAIN_DIRS.get(platform, CANDIDATE_BRAIN_DIRS["agy"])
    existing = []
    for raw in candidates:
        p = Path(os.path.expanduser(raw))
        if p.is_dir():
            existing.append(str(p.resolve()).replace("\\", "/"))
    if existing:
        return existing
    return [str(Path(os.path.expanduser(raw))).replace("\\", "/") for raw in candidates]


def load_init_menu(skill_dir):
    try:
        text = (Path(skill_dir) / "templates" / "init_menu.md").read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^- (M\d+)\s+(.+)$", line.rstrip())
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2).strip()
    return out


def check_pair_symmetry(normalized):
    """針對已提供的 {seat_int: model_str} 檢查對立席位組（1-2, 3-4, 5-6）是否算力對稱。"""
    errors = []
    for a, b in OPPOSING_PAIRS:
        if a not in normalized or b not in normalized:
            continue
        ma, mb = normalized[a], normalized[b]
        if ma == mb:
            continue
        ta, tb = KNOWN_MODEL_TIERS.get(ma), KNOWN_MODEL_TIERS.get(mb)
        if ta is not None and tb is not None and ta != tb:
            errors.append(
                f"算力不對稱違規：對立席位 {a}（{ma}）與席位 {b}（{mb}）算力等級不同，"
                "必須使用相同等級的模型以避免傾向偏誤"
            )
        elif (ma == "inherit") != (mb == "inherit"):
            errors.append(
                f"算力不對稱違規：對立席位 {a}（{ma}）與席位 {b}（{mb}）不可一席為 inherit、另一席指定固定模型"
            )
    return errors


def check_symmetry(seat_models):
    """檢查 6 席模型配置是否完整且符合對立席位（1-2, 3-4, 5-6）算力對稱原則。"""
    errors = []
    if not isinstance(seat_models, dict):
        return ["seat_models 必須是物件（鍵為 '1'..'6'）"]
    normalized = {}
    for n in range(1, 7):
        val = seat_models.get(str(n)) or seat_models.get(n)
        if not isinstance(val, str) or not val.strip():
            errors.append(f"缺少席位 {n} 的模型設定")
        else:
            normalized[n] = val.strip()
    if errors:
        return errors
    return check_pair_symmetry(normalized)


def validate_config(cfg, skill_dir=None):
    """驗證 .council/config.json 結構與算力對稱規則，回傳錯誤訊息清單。"""
    if not isinstance(cfg, dict):
        return ["config.json 內容必須是 JSON 物件"]
    errors = []
    platform = cfg.get("platform")
    valid_platforms = available_platforms(skill_dir) if skill_dir else ["agy"]
    if not isinstance(platform, str) or not platform.strip():
        errors.append("缺少 platform 欄位")
    elif skill_dir and platform not in valid_platforms:
        errors.append(f"不支援的 platform：{platform!r}（可用：{', '.join(valid_platforms)}）")

    brain_dirs = cfg.get("brain_dirs")
    if not isinstance(brain_dirs, list) or not brain_dirs or not all(isinstance(d, str) and d.strip() for d in brain_dirs):
        errors.append("brain_dirs 必須是非空的路徑字串陣列")

    preset = cfg.get("model_preset")
    if preset not in ("M1", "M2", "M3", "M4", "M5"):
        errors.append(f"model_preset 不合法：{preset!r}（必須為 M1–M5）")

    seat_models = cfg.get("seat_models")
    sym_errors = check_symmetry(seat_models)
    errors.extend(sym_errors)

    if not sym_errors and preset in PRESET_TO_MODEL:
        expected = PRESET_TO_MODEL[preset]
        for n in range(1, 7):
            actual = (seat_models.get(str(n)) or seat_models.get(n)).strip()
            if actual != expected:
                errors.append(f"model_preset 為 {preset}（應全席為 {expected}），但席位 {n} 為 {actual}")
    return errors


def parse_seat_models_arg(raw):
    """解析 '1=pro,2=pro,3=flash,4=flash,5=pro,6=pro' 字串為 {'1': 'pro', ...}。"""
    out = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"格式錯誤：{item!r}（應為 席位=模型，例如 1=pro）")
        k, v = item.split("=", 1)
        k, v = k.strip(), v.strip()
        if k not in {"1", "2", "3", "4", "5", "6"} or not v:
            raise ValueError(f"席位編號或模型名稱不合法：{item!r}")
        out[k] = v
    return out


def build_config(skill_dir, platform="agy", brain_dirs=None, preset="M1", seat_models=None):
    b_dirs = brain_dirs or detect_brain_dirs(platform)
    if preset in PRESET_TO_MODEL and not seat_models:
        m = PRESET_TO_MODEL[preset]
        s_models = {str(n): m for n in range(1, 7)}
    else:
        s_models = {str(k): str(v).strip() for k, v in (seat_models or {}).items()}
    cfg = {
        "version": CONFIG_VERSION,
        "platform": platform,
        "brain_dirs": [str(Path(os.path.expanduser(d))).replace("\\", "/") for d in b_dirs],
        "model_preset": preset,
        "seat_models": s_models,
    }
    errs = validate_config(cfg, skill_dir)
    return cfg, errs


def config_path_for(workspace=None, run_dir=None, explicit_config=None):
    if explicit_config:
        return Path(explicit_config)
    if workspace:
        return Path(workspace) / ".council" / "config.json"
    if run_dir:
        r = Path(run_dir).resolve()
        # 標準結構：<workspace>/.council/runs/<timestamp> -> 往上兩層是 .council
        if r.parent.name == "runs":
            return r.parent.parent / "config.json"
        return r.parent / "config.json"
    return Path.cwd() / ".council" / "config.json"


def probe(skill_dir, workspace):
    cfg_p = config_path_for(workspace=workspace)
    platforms = available_platforms(skill_dir)
    detected_brains = detect_brain_dirs("agy")
    menu = load_init_menu(skill_dir)
    status = {
        "config_path": str(cfg_p).replace("\\", "/"),
        "exists": cfg_p.is_file(),
        "valid": False,
        "errors": [],
        "config": None,
        "detected": {
            "platforms": platforms,
            "default_platform": platforms[0],
            "brain_dirs": detected_brains,
        },
        "menu": menu,
    }
    if cfg_p.is_file():
        try:
            cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
            errs = validate_config(cfg, skill_dir)
            status["config"] = cfg
            status["errors"] = errs
            status["valid"] = len(errs) == 0
        except Exception as e:
            status["errors"] = [f"無法解析 config.json：{e!r}"]
    return status


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--workspace", default=".", help="工作區根目錄（預設為目前目錄）")
    ap.add_argument("--config", default=None, help="直接指定 config.json 路徑")
    ap.add_argument("--probe", action="store_true", help="探測環境並檢查 .council/config.json 狀態")
    ap.add_argument("--preset", choices=["M1", "M2", "M3", "M4", "M5"], default=None,
                    help="寫入指定的模型編制選項（M1–M5）")
    ap.add_argument("--platform", default="agy", help="指定執行平台 adapter（預設 agy）")
    ap.add_argument("--brain-dir", action="append", default=None, help="覆寫 brain_dirs（可重複）")
    ap.add_argument("--seat-models", default=None,
                    help="M5 自訂各席模型，格式：1=pro,2=pro,3=flash,4=flash,5=pro,6=pro")
    a = ap.parse_args()

    skill_dir = Path(a.skill_dir)
    cfg_p = config_path_for(workspace=a.workspace, explicit_config=a.config)

    if a.probe or a.preset is None:
        st = probe(skill_dir, a.workspace)
        if a.config:
            st["config_path"] = str(cfg_p).replace("\\", "/")
        print(json.dumps(st, ensure_ascii=False, indent=2))
        sys.exit(0 if (a.probe or st["valid"]) else 1)

    seat_models = None
    if a.seat_models:
        try:
            seat_models = parse_seat_models_arg(a.seat_models)
        except ValueError as e:
            print(f"錯誤：{e}", file=sys.stderr)
            sys.exit(2)

    cfg, errs = build_config(
        skill_dir=skill_dir,
        platform=a.platform,
        brain_dirs=a.brain_dir,
        preset=a.preset,
        seat_models=seat_models,
    )
    if errs:
        for e in errs:
            print(f"錯誤：{e}", file=sys.stderr)
        sys.exit(2)

    cfg_p.parent.mkdir(parents=True, exist_ok=True)
    cfg_p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已寫入設定檔：{cfg_p}")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
