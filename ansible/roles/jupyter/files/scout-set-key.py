#!/usr/bin/env python3
"""Save your AI gateway key for Claude Code, Codex, and VS Code chat.

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
CHAT_MODELS = Path.home() / ".local/share/code-server/User/chatLanguageModels.json"
STARTER = Path("/opt/scout/user-starter")


def restore_starter_files():
    """Copy in any starter file the user no longer has, as server start does."""
    try:
        manifest = (STARTER / "manifest").read_text().splitlines()
    except FileNotFoundError:
        return
    for line in manifest:
        name, _, rel = line.strip().partition(" ")
        rel = rel.strip()
        target = Path.home() / rel
        if not rel or target.exists() or target.is_symlink():
            continue
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write((STARTER / name).read_bytes())


def read_json(path, kind=dict):
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return kind()
    except json.JSONDecodeError as err:
        sys.exit(f"{path} is not valid JSON ({err}); fix or remove it, then retry.")
    if not isinstance(data, kind):
        noun = "an object" if kind is dict else "a list"
        sys.exit(f"{path} must hold a JSON {noun}; fix or remove it, then retry.")
    return data


def write_json(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def set_chat_model_keys(providers, key, replaceable):
    """Set x-api-key on chat models whose header holds the placeholder or an old key."""
    changed = False
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        for model in provider.get("models") or []:
            headers = model.get("requestHeaders") if isinstance(model, dict) else None
            if not isinstance(headers, dict):
                continue
            for name, value in headers.items():
                if name.lower() == "x-api-key" and value in replaceable:
                    headers[name] = key
                    changed = True
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stdin", action="store_true", help="read the key from stdin")
    args = parser.parse_args()

    key = sys.stdin.readline() if args.stdin else getpass.getpass("AI gateway key: ")
    key = key.strip()
    if not key or key == PLACEHOLDER or any(c.isspace() for c in key):
        sys.exit("That doesn't look like a key; nothing was changed.")

    restore_starter_files()
    claude = read_json(CLAUDE_SETTINGS)
    codex = read_json(CODEX_AUTH)
    chat = read_json(CHAT_MODELS, kind=list)
    env = claude.setdefault("env", {})
    if not isinstance(env, dict):
        sys.exit(f'"env" in {CLAUDE_SETTINGS} must be an object; nothing was changed.')

    old_keys = {env.get("ANTHROPIC_AUTH_TOKEN"), codex.get("OPENAI_API_KEY")}
    replaceable = {PLACEHOLDER} | {k for k in old_keys if isinstance(k, str) and k}
    env["ANTHROPIC_AUTH_TOKEN"] = key
    codex["OPENAI_API_KEY"] = key
    saved = [CLAUDE_SETTINGS, CODEX_AUTH]
    write_json(CLAUDE_SETTINGS, claude)
    write_json(CODEX_AUTH, codex)
    if set_chat_model_keys(chat, key, replaceable):
        write_json(CHAT_MODELS, chat)
        saved.append(CHAT_MODELS)
    if not args.stdin:
        print(
            f"Saved to {', '.join(map(str, saved))}. Start a new claude session, open a new "
            "terminal before running codex, and reload the VS Code window for chat."
        )


if __name__ == "__main__":
    main()
