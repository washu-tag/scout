"""Encode XNAT's first-boot password using its native delegating encoder format."""

import configparser
import json
import sys

import bcrypt


def encode(password, previous):
    raw = password.encode("utf-8")
    if not raw or len(raw) > 72:
        raise ValueError("XNAT's bcrypt password must contain 1 to 72 UTF-8 bytes")
    if previous.startswith("{bcrypt}"):
        try:
            if bcrypt.checkpw(raw, previous[len("{bcrypt}") :].encode("ascii")):
                return previous
        except (ValueError, UnicodeEncodeError):
            pass
    return "{bcrypt}" + bcrypt.hashpw(
        raw, bcrypt.gensalt(rounds=10, prefix=b"2a")
    ).decode("ascii")


if __name__ == "__main__":
    values = json.load(sys.stdin)
    previous = ""
    if values.get("previous_ini"):
        prefs = configparser.RawConfigParser()
        prefs.read_string(values["previous_ini"])
        previous = prefs.get("system", "defaultAdminPassword", fallback="")
    print(encode(values["password"], previous))
