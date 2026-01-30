from typing import Optional


def normalize_script_config_name(script_config: Optional[str]) -> Optional[str]:
    if not script_config:
        return None
    if script_config.lower().endswith(".yml"):
        return script_config
    return f"{script_config}.yml"
