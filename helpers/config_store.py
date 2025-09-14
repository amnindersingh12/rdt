import json
import os
from typing import Dict, List

from config import PyroConf


CONFIG_PATH = "runtime_config.json"


def _default_config() -> Dict:
    sources: List[str] = []
    if PyroConf.SOURCE_CHANNELS:
        sources = [s.strip() for s in PyroConf.SOURCE_CHANNELS.split(",") if s.strip()]
    return {
        "forward_enabled": bool(PyroConf.FORWARD_ENABLED),
        "destination_channel": PyroConf.DESTINATION_CHANNEL or "",
        "source_channels": sources,
    }


def load_config() -> Dict:
    cfg = _default_config()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update({
                    "forward_enabled": bool(data.get("forward_enabled", cfg["forward_enabled"])),
                    "destination_channel": str(data.get("destination_channel", cfg["destination_channel"] or "")),
                    "source_channels": list(data.get("source_channels", cfg["source_channels"] or [])),
                })
        except Exception:
            pass
    return cfg


def save_config(cfg: Dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def add_source_channel(source: str) -> Dict:
    cfg = load_config()
    src = source.strip()
    if src and src not in cfg["source_channels"]:
        cfg["source_channels"].append(src)
        save_config(cfg)
    return cfg


def remove_source_channel(source: str) -> Dict:
    cfg = load_config()
    src = source.strip()
    if src in cfg["source_channels"]:
        cfg["source_channels"].remove(src)
        save_config(cfg)
    return cfg


def clear_sources() -> Dict:
    cfg = load_config()
    cfg["source_channels"] = []
    save_config(cfg)
    return cfg


def set_target_channel(target: str) -> Dict:
    cfg = load_config()
    cfg["destination_channel"] = target.strip()
    save_config(cfg)
    return cfg


def set_forward_enabled(enabled: bool) -> Dict:
    cfg = load_config()
    cfg["forward_enabled"] = bool(enabled)
    save_config(cfg)
    return cfg
