import json
import os
from typing import Dict, List

from config import PyroConf


CONFIG_PATH = "runtime_config.json"


def _default_config() -> Dict:
    return {
        "forward_enabled": False,
        "destination_channel": "",
        "source_channels": [],
        "mirror_enabled": False,
        "mirror_rules": {},
        "replication_enabled": False,
        "replication_mappings": [],
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
                    "mirror_enabled": bool(data.get("mirror_enabled", cfg["mirror_enabled"])),
                    "mirror_rules": dict(data.get("mirror_rules", cfg["mirror_rules"] or {})),
                    "replication_enabled": bool(data.get("replication_enabled", cfg["replication_enabled"])),
                    "replication_mappings": list(data.get("replication_mappings", cfg["replication_mappings"] or [])),
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


def set_mirror_enabled(enabled: bool) -> Dict:
    cfg = load_config()
    cfg["mirror_enabled"] = bool(enabled)
    save_config(cfg)
    return cfg


def add_mirror_rule(source: str, targets: List[str]) -> Dict:
    cfg = load_config()
    rules = cfg.get("mirror_rules")
    if not isinstance(rules, dict):
        rules = {}
    src = source.strip()
    if not src:
        return cfg
    existing = rules.get(src)
    if not isinstance(existing, list):
        existing = []
    for t in targets:
        tt = str(t).strip()
        if tt and tt not in existing:
            existing.append(tt)
    rules[src] = existing
    cfg["mirror_rules"] = rules
    save_config(cfg)
    return cfg


def remove_mirror_rule(source: str, targets: List[str] | None = None) -> Dict:
    cfg = load_config()
    rules = cfg.get("mirror_rules")
    if not isinstance(rules, dict):
        rules = {}
    src = source.strip()
    if not src:
        return cfg
    if targets is None:
        rules.pop(src, None)
    else:
        existing = rules.get(src)
        if isinstance(existing, list):
            to_remove = {str(t).strip() for t in targets if str(t).strip()}
            existing = [t for t in existing if t not in to_remove]
            if existing:
                rules[src] = existing
            else:
                rules.pop(src, None)
    cfg["mirror_rules"] = rules
    save_config(cfg)
    return cfg


def clear_mirror_rules() -> Dict:
    cfg = load_config()
    cfg["mirror_rules"] = {}
    save_config(cfg)
    return cfg
