#!/usr/bin/env python3
"""Generate a scoped LiteLLM virtual key (sk-...) via /key/generate and
record it in keys.secret (gitignored) so issued keys stay tracked locally.

By default (no --models given) the key gets access to every model in
litellm-config.yaml, with per-model rpm/tpm limits matching the issuing
provider's tier — see PROVIDER_LIMIT_ENV / .env.example.

Usage:
  ./generate-key.py --alias my-team [--models claude-sonnet-4-6,gemini-3.5-flash] \
                    [--budget 20] [--rpm 60] [--tpm 100000] [--duration 30d]
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
CONFIG_FILE = ROOT / "litellm-config.yaml"
SECRET_FILE = ROOT / "keys.secret"
PROXY_URL = os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:4000")

# litellm_params.model prefix -> .env vars holding that provider's tier limits
PROVIDER_LIMIT_ENV = {
    "anthropic": ("ANTHROPIC_RPM_LIMIT", "ANTHROPIC_TPM_LIMIT"),
    "gemini": ("GEMINI_RPM_LIMIT", "GEMINI_TPM_LIMIT"),
}


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


def load_models():
    """Return {model_name: provider} for every entry in litellm-config.yaml."""
    config = yaml.safe_load(CONFIG_FILE.read_text())
    models = {}
    for entry in config.get("model_list", []):
        name = entry.get("model_name")
        target = entry.get("litellm_params", {}).get("model", "")
        provider = target.split("/", 1)[0] if "/" in target else target
        if name:
            models[name] = provider
    return models


def provider_limits(env, provider):
    """Look up (rpm, tpm) for a provider from .env; None if unset."""
    spec = PROVIDER_LIMIT_ENV.get(provider)
    if not spec:
        return None
    rpm_key, tpm_key = spec
    try:
        rpm = int(env[rpm_key])
        tpm = int(env[tpm_key])
    except (KeyError, ValueError):
        return None
    return rpm, tpm


def build_provider_matched_limits(env, model_names, all_models):
    """Build model_rpm_limit / model_tpm_limit dicts that
    mirror each model's upstream provider tier, for the given model_names."""
    model_rpm, model_tpm = {}, {}
    skipped = []
    for name in model_names:
        provider = all_models.get(name)
        limits = provider_limits(env, provider) if provider else None
        if not limits:
            skipped.append(name)
            continue
        rpm, tpm = limits
        model_rpm[name] = rpm
        model_tpm[name] = tpm
    return model_rpm, model_tpm, skipped


def parse_args():
    p = argparse.ArgumentParser(description="Generate a LiteLLM virtual key")
    p.add_argument("--alias", required=True, help="Name to identify this key (e.g. team or client name)")
    p.add_argument("--models", help="Comma-separated model_name list (default: every model in litellm-config.yaml)")
    p.add_argument("--budget", type=float, help="Overall max spend in USD before the key stops working")
    p.add_argument("--rpm", type=int, help="Overall requests-per-minute limit (overrides provider-matched per-model limits)")
    p.add_argument("--tpm", type=int, help="Overall tokens-per-minute limit (overrides provider-matched per-model limits)")
    p.add_argument("--duration", help="Key lifetime, e.g. '30d', '24h' (default: no expiry)")
    return p.parse_args()


def generate_key(master_key, args, env, all_models):
    model_names = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else list(all_models)

    body = {"key_alias": args.alias, "models": model_names, "metadata": {"alias": args.alias}}

    skipped = []
    if args.rpm is not None:
        body["rpm_limit"] = args.rpm
    if args.tpm is not None:
        body["tpm_limit"] = args.tpm
    if args.rpm is None and args.tpm is None:
        model_rpm, model_tpm, skipped = build_provider_matched_limits(env, model_names, all_models)
        if model_rpm:
            body["model_rpm_limit"] = model_rpm
            body["model_tpm_limit"] = model_tpm

    if args.budget is not None:
        body["max_budget"] = args.budget
    if args.duration:
        body["duration"] = args.duration

    req = urllib.request.Request(
        f"{PROXY_URL}/key/generate",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {master_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp), skipped
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
            err_json = json.loads(err_body)
            err_msg = err_json.get("error", {}).get("message", "")
        except Exception:
            err_msg = ""
        if err_msg:
            raise Exception(f"HTTP Error {exc.code}: {err_msg}")
        raise exc


def load_secrets():
    if SECRET_FILE.exists():
        return json.loads(SECRET_FILE.read_text())
    return []


def store_secret(record):
    records = load_secrets()
    records.append(record)
    SECRET_FILE.write_text(json.dumps(records, indent=2) + "\n")
    SECRET_FILE.chmod(0o600)


def main():
    args = parse_args()
    env = load_env()
    master_key = env.get("LITELLM_MASTER_KEY", "").strip()
    if not master_key or master_key.startswith("your_") or master_key.endswith("1234"):
        sys.exit("LITELLM_MASTER_KEY missing or still a placeholder in .env — set it first.")

    all_models = load_models()
    if not all_models:
        sys.exit(f"No models found in {CONFIG_FILE.name} — nothing to grant access to.")

    try:
        result, skipped = generate_key(master_key, args, env, all_models)
    except Exception as exc:
        sys.exit(f"Failed to reach {PROXY_URL}/key/generate: {exc}")

    record = {
        "alias": args.alias,
        "key": result.get("key"),
        "key_name": result.get("key_name"),
        "models": result.get("models"),
        "max_budget": result.get("max_budget"),
        "rpm_limit": result.get("rpm_limit"),
        "tpm_limit": result.get("tpm_limit"),
        "model_rpm_limit": result.get("model_rpm_limit"),
        "model_tpm_limit": result.get("model_tpm_limit"),
        "expires": result.get("expires"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    store_secret(record)

    print(f"Generated key for '{args.alias}':")
    print(record["key"])
    print(f"Models: {', '.join(record['models'] or [])}")
    if skipped:
        print(
            f"Note: no provider rate-limit profile in .env for: {', '.join(skipped)} "
            f"— granted access without provider-matched per-model limits."
        )
    print(f"Recorded in {SECRET_FILE.name}")


if __name__ == "__main__":
    main()
