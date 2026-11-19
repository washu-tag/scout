#!/usr/bin/env python3
"""Save your AI gateway key for Claude Code and Codex.

Run it and paste the key at the prompt, or pipe the key in with --stdin.
"""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

PLACEHOLDER = "REPLACE_WITH_YOUR_GATEWAY_KEY"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
CODEX_AUTH = Path.home() / ".codex" / "auth.json"


def read_json(path):
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as err:
        sys.exit(f"{path} is not valid JSON ({err}); fix or remove it, then retry.")
    if not isinstance(data, dict):
        sys.exit(f"{path} must hold a JSON object; fix or remove it, then retry.")
    return data


def write_json(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stdin", action="store_true", help="read the key from stdin")
    args = parser.parse_args()

    key = sys.stdin.readline() if args.stdin else getpass.getpass("AI gateway key: ")
    key = key.strip()
    if not key or key == PLACEHOLDER or any(c.isspace() for c in key):
        sys.exit("That doesn't look like a key; nothing was changed.")

    claude = read_json(CLAUDE_SETTINGS)
    codex = read_json(CODEX_AUTH)
    env = claude.setdefault("env", {})
    if not isinstance(env, dict):
        sys.exit(f'"env" in {CLAUDE_SETTINGS} must be an object; nothing was changed.')
    env["ANTHROPIC_AUTH_TOKEN"] = key
    codex["OPENAI_API_KEY"] = key
    write_json(CLAUDE_SETTINGS, claude)
    write_json(CODEX_AUTH, codex)
    if not args.stdin:
        print(
            f"Saved to {CLAUDE_SETTINGS} and {CODEX_AUTH}. "
            "Start a new claude session, and open a new terminal before running codex."
        )


if __name__ == "__main__":
    main()
