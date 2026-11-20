"""設定檔讀寫（~/.config/icam-linux/cameras.json，權限 600）。"""

import json
import os
from pathlib import Path

CFG_DIR = Path(os.environ.get("ICAM_CONFIG_DIR", Path.home() / ".config" / "icam-linux"))
CFG_FILE = CFG_DIR / "cameras.json"

DEFAULT = {
    "cameras": [],
    "tile": [640, 360],
    "fps": 15,
    "transport": "tcp",
    "snapshot_dir": str(Path.home() / "Pictures" / "icam"),
}


def load():
    cfg = dict(DEFAULT)
    if CFG_FILE.exists():
        try:
            data = json.loads(CFG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except (OSError, json.JSONDecodeError):
            pass
    cfg["cameras"] = [c for c in cfg.get("cameras", []) if isinstance(c, dict) and c.get("url")]
    _merge_env_secrets(cfg)
    return cfg


def _merge_env_secrets(cfg):
    """dvr.env（600，DVR_USER=/DVR_PASS=）有的話優先蓋掉 cameras.json 裡的帳密，
    這樣帳密可以只放在一個地方。"""
    env = CFG_DIR / "dvr.env"
    if not env.exists():
        return
    vals = {}
    try:
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return
    dvr = cfg.setdefault("dvr", {})
    if vals.get("DVR_USER"):
        dvr["user"] = vals["DVR_USER"]
    if vals.get("DVR_PASS"):
        dvr["password"] = vals["DVR_PASS"]
    if vals.get("DVR_HOST"):
        dvr["host"] = vals["DVR_HOST"]
    if vals.get("DVR_PORT"):
        try:
            dvr["port"] = int(vals["DVR_PORT"])
        except ValueError:
            pass


def save(cfg):
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    out = dict(cfg)
    env = CFG_DIR / "dvr.env"
    if env.exists() and isinstance(out.get("dvr"), dict) and env.read_text(
            encoding="utf-8", errors="ignore").find("DVR_PASS=") >= 0:
        # 帳密已在 dvr.env，不要重複寫進 cameras.json
        out["dvr"] = {k: v for k, v in out["dvr"].items() if k != "password"}
    tmp = CFG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(CFG_FILE)
