#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import yaml


DEFAULT_SOURCE = Path.home() / "clashctl/resources/profiles/12.yaml"
DEFAULT_TARGET = Path.home() / ".config/polydata/clob-profile12-mihomo.yaml"
DEFAULT_STATE_DIR = Path.home() / ".config/polydata/clob-profile12-state"


def _replace_top_level_value(lines: list[str], key: str, value: str) -> bool:
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{prefix} {value}"
            return True
    return False


def render_config(source: Path, target: Path, *, mixed_port: int, controller: str) -> None:
    config = yaml.safe_load(source.read_text(encoding="utf-8", errors="ignore"))
    if not isinstance(config, dict):
        raise ValueError("profile 12 config must be a YAML mapping")
    config.update(
        {
            "mixed-port": int(mixed_port),
            "allow-lan": False,
            "bind-address": "127.0.0.1",
            "external-controller": controller,
            "log-level": "warning",
        }
    )
    dns = config.get("dns") if isinstance(config.get("dns"), dict) else {}
    direct_nameservers = ["223.5.5.5", "119.29.29.29", "114.114.114.114"]
    dns.update(
        {
            "respect-rules": False,
            "default-nameserver": direct_nameservers,
            "proxy-server-nameserver": direct_nameservers,
            "nameserver": direct_nameservers,
        }
    )
    dns.pop("fallback", None)
    dns.pop("fallback-filter", None)
    config["dns"] = dns
    groups = [item for item in config.get("proxy-groups") or [] if isinstance(item, dict)]
    groups = [item for item in groups if item.get("name") != "CLOB-POOL"]
    for group in groups:
        if group.get("name") in {"\U0001f680 \u8282\u70b9\u9009\u62e9", "\U0001f41f \u6f0f\u7f51\u4e4b\u9c7c"}:
            group["proxies"] = [str(item) for item in group.get("proxies") or [] if str(item) != "CLOB-POOL"]
    config["proxy-groups"] = groups

    rendered = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a dedicated Mihomo config for CLOB using clashsub profile 12.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--target", default=str(DEFAULT_TARGET))
    parser.add_argument("--mixed-port", type=int, default=17890)
    parser.add_argument("--controller", default="127.0.0.1:19090")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    target = Path(args.target).expanduser().resolve()
    state_dir = Path(args.state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)

    render_config(source, target, mixed_port=args.mixed_port, controller=args.controller)
    print(f"rendered={target}")
    print(f"state_dir={state_dir}")
    print(f"mixed_port={int(args.mixed_port)}")
    print(f"controller={args.controller}")


if __name__ == "__main__":
    main()
