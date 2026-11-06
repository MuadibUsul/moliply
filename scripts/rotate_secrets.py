#!/usr/bin/env python3
"""Generate a fresh, correctly-shaped block of secrets for a MOLIPLY deployment.

Prints `.env`-formatted lines to stdout and nothing else, so it composes:

    python scripts/rotate_secrets.py > /tmp/new-secrets.env
    # review, then merge into the real .env / cce.env / upstream .env

Formats match what the code actually parses:

* AES keys are base64 of 32 raw bytes (`crypto._encryption_key`, `cce.core.crypto._load_key`).
* JWT secrets are URL-safe text, long enough for HS256.
* The TOTP value is a base32 string that `pyotp.TOTP` accepts.
* Tokens are hex so they survive shell/`.env` quoting untouched.

Rotating `MASTER_AES_KEY` or `CCE_MASTER_KEY` makes existing ciphertext
undecryptable. See the warning printed at the end.
"""

from __future__ import annotations

import argparse
import base64
import secrets
import sys

import pyotp

# Rendered in the same order the operator should paste them.
SECRETS = (
    ("MASTER_AES_KEY", "main store: card PAN/CVV, sessions, delivered payloads"),
    ("CCE_MASTER_KEY", "CCE: subscription cards, Claude credential blobs"),
    ("ADMIN_JWT_SECRET", "admin console JWT"),
    ("MOLIPLY_ADMIN_JWT_SECRET", "same value as ADMIN_JWT_SECRET, read by /cce"),
    ("USER_JWT_SECRET", "customer order-account JWT"),
    ("CCE_ADMIN_TOKEN", "CCE scripted admin access (x-admin-token)"),
    ("UPSTREAM_KEY", "store -> upstream-service merchant key"),
    ("UPSTREAM_SECRET", "store -> upstream-service HMAC secret"),
    ("UPSTREAM_ADMIN_TOKEN", "upstream-service operator console"),
    ("ADMIN_TOTP_SECRET", "admin Google Authenticator seed"),
)


def aes256_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def text_secret(bytes_: int = 48) -> str:
    return secrets.token_urlsafe(bytes_)


def hex_secret(bytes_: int) -> str:
    return secrets.token_hex(bytes_)


def build() -> dict[str, str]:
    admin_jwt = text_secret()
    return {
        "MASTER_AES_KEY": aes256_key(),
        "CCE_MASTER_KEY": aes256_key(),
        "ADMIN_JWT_SECRET": admin_jwt,
        "MOLIPLY_ADMIN_JWT_SECRET": admin_jwt,
        "USER_JWT_SECRET": text_secret(),
        "CCE_ADMIN_TOKEN": hex_secret(32),
        "UPSTREAM_KEY": hex_secret(16),
        "UPSTREAM_SECRET": hex_secret(32),
        "UPSTREAM_ADMIN_TOKEN": hex_secret(24),
        "ADMIN_TOTP_SECRET": pyotp.random_base32(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--prefix",
        default="",
        help="prepended to every variable name, e.g. 'PROD_'",
    )
    parser.add_argument(
        "--without",
        default="",
        help="comma-separated variable names to omit (keep the values you already have)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only emit KEY=VALUE lines, no comments or warnings",
    )
    args = parser.parse_args(argv)

    skip = {name.strip() for name in args.without.split(",") if name.strip()}
    unknown = skip - {name for name, _ in SECRETS}
    if unknown:
        parser.error(f"unknown --without names: {', '.join(sorted(unknown))}")

    values = build()
    if not args.quiet:
        print("# MOLIPLY secret rotation block")
        print("# Merge only the lines you intend to rotate; keep the rest.")
        print()

    emitted = 0
    for name, description in SECRETS:
        if name in skip:
            continue
        if not args.quiet:
            print(f"# {description}")
        print(f"{args.prefix}{name}={values[name]}")
        emitted += 1

    if not args.quiet:
        print()
        print(f"# {emitted} value(s) generated", file=sys.stderr)
        print("# NOTE: a new MASTER_AES_KEY or CCE_MASTER_KEY makes existing", file=sys.stderr)
        print("#       ciphertext undecryptable. Rotate one at a time, and only", file=sys.stderr)
        print("#       with a current database backup in hand.", file=sys.stderr)
        print("# ADMIN_TOTP_SECRET needs re-enrolment: the QR code changes.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
