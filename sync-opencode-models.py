#!/usr/bin/env python3
"""Pull live model metadata from the LiteLLM proxy's /model/info endpoint
and merge it into opencode.json (cost, context limits, tool/vision support).

Run after editing litellm-config.yaml and restarting the proxy, so opencode
always reflects what the backend actually supports/charges.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
CONFIG_FILE = ROOT / "opencode.json"
PROXY_URL = os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:4000")


def load_env():
    env = dict(os.environ)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env.setdefault(key.strip(), val.strip())
    return env


def fetch_model_info(master_key):
    req = urllib.request.Request(
        f"{PROXY_URL}/model/info",
        headers={"Authorization": f"Bearer {master_key}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)["data"]


def to_per_million(cost_per_token):
    if cost_per_token is None:
        return None
    return round(cost_per_token * 1_000_000, 4)


def derive_fields(info):
    fields = {}

    context = info.get("max_input_tokens") or info.get("max_tokens")
    output = info.get("max_output_tokens")
    if context and output:
        fields["limit"] = {"context": context, "output": output}

    cost = {}
    for opencode_key, litellm_key in (
        ("input", "input_cost_per_token"),
        ("output", "output_cost_per_token"),
        ("cache_read", "cache_read_input_token_cost"),
        ("cache_write", "cache_creation_input_token_cost"),
    ):
        per_million = to_per_million(info.get(litellm_key))
        if per_million is not None:
            cost[opencode_key] = per_million
    if cost:
        fields["cost"] = cost

    if "supports_function_calling" in info:
        fields["tool_call"] = bool(info["supports_function_calling"])
    if "supports_vision" in info:
        fields["attachment"] = bool(info["supports_vision"])

    # Deliberately not syncing "reasoning": LiteLLM's supports_reasoning reflects
    # base-model capability, but opencode.json uses the field to distinguish the
    # dedicated "-thinking" variant entries from plain ones — mirroring it would
    # make opencode request reasoning on every model, raising cost.

    return fields


def merge(config, model_infos):
    changed = False
    for provider in config.get("provider", {}).values():
        models = provider.get("models", {})
        for model_name, model_cfg in models.items():
            info = model_infos.get(model_name)
            if not info:
                continue
            for key, value in derive_fields(info).items():
                if model_cfg.get(key) != value:
                    model_cfg[key] = value
                    changed = True
    return changed


def main():
    env = load_env()
    master_key = env.get("LITELLM_MASTER_KEY", "").strip()
    if not master_key or master_key.startswith("your_") or master_key.endswith("1234"):
        sys.exit("LITELLM_MASTER_KEY missing or still a placeholder in .env — set it first.")

    try:
        data = fetch_model_info(master_key)
    except Exception as exc:
        sys.exit(f"Failed to reach {PROXY_URL}/model/info: {exc}")

    model_infos = {entry["model_name"]: entry.get("model_info") or {} for entry in data}

    config = json.loads(CONFIG_FILE.read_text())
    if merge(config, model_infos):
        CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
        print(f"Updated {CONFIG_FILE.name} with live model metadata.")
    else:
        print("No changes — opencode.json already matches proxy model info.")


if __name__ == "__main__":
    main()
