#!/usr/bin/env python3
"""W1-F7 — prove a captured container log carries no credential, redacting any it does.

Usage: log_credential_scan.py --log FILE --container ID

Two independent checks, then an in-place redaction and a second, residual scan:

* the container's OWN secret values — every `.Config.Env` entry whose name looks like a
  credential (token, secret, password, key, DSN, database URL), plus the password part of any
  `scheme://user:password@` value — searched for literally;
* credential SHAPES — a bearer header, a Meta access token, an OpenAI or Resend key, URL
  userinfo with a password, a PEM private-key header.

Nothing it finds is ever printed: only labels and counts, and the file's final SHA-256. The
log is rewritten atomically with mode 0600. Exit 0 only when the residual scan is clean.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

_SECRET_NAME = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE|CREDENTIAL|DATABASE_URL|_DSN|_KEY$)", re.I)
_URL_PASSWORD = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:([^\s@/]+)@")
_SHAPES = {
    "bearer": re.compile(r"Bearer\s+[A-Za-z0-9._\-~+/=]{16,}"),
    "meta_token": re.compile(r"\bEAA[A-Za-z0-9]{20,}"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "resend_key": re.compile(r"\bre_[A-Za-z0-9_]{16,}"),
    "url_password": _URL_PASSWORD,
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
_MIN_SECRET_LEN = 8


def container_secrets(container: str) -> list[str]:
    raw = subprocess.run(["docker", "inspect", "--format", "{{json .Config.Env}}", container],
                         check=True, capture_output=True, text=True).stdout
    values: set[str] = set()
    for entry in json.loads(raw) or []:
        name, _, value = entry.partition("=")
        if _SECRET_NAME.search(name) and len(value) >= _MIN_SECRET_LEN:
            values.add(value)
        for match in _URL_PASSWORD.finditer(value):
            if len(match.group(1)) >= _MIN_SECRET_LEN:
                values.add(match.group(1))
    # longest first, so a value that contains another is redacted whole
    return sorted(values, key=len, reverse=True)


def scan(text: str, secrets: list[str]) -> dict[str, int]:
    found = {"container_secret": sum(text.count(v) for v in secrets)}
    for label, pattern in _SHAPES.items():
        found[label] = len(pattern.findall(text))
    return {k: v for k, v in found.items() if v}


def redact(text: str, secrets: list[str]) -> str:
    for value in secrets:
        text = text.replace(value, "[REDACTED:container_secret]")
    for label, pattern in _SHAPES.items():
        text = pattern.sub(f"[REDACTED:{label}]", text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--container", required=True)
    args = ap.parse_args()
    secrets = container_secrets(args.container)
    with open(args.log, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    before = scan(text, secrets)
    if before:
        cleaned = redact(text, secrets)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(args.log)))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(cleaned)
        os.chmod(tmp, 0o600)
        os.replace(tmp, args.log)
        text = cleaned
    os.chmod(args.log, 0o600)
    residual = scan(text, secrets)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    print(f"credential_scan: secret_values_checked={len(secrets)} "
          f"shapes_checked={len(_SHAPES)} found_before={json.dumps(before, sort_keys=True)} "
          f"residual={json.dumps(residual, sort_keys=True)} lines={text.count(chr(10))} "
          f"sha256={digest}")
    return 0 if not residual else 2


if __name__ == "__main__":
    sys.exit(main())
