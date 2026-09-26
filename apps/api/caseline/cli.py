"""Operator tooling.

    python -m caseline.cli create-principal --name alice --role operator
    python -m caseline.cli create-principal --name firm-a-intake --role firm_user --firm demo-firm-a
    python -m caseline.cli deactivate-principal --name alice
    python -m caseline.cli generate-keys          # new CASELINE_FIELD_ENCRYPTION_KEY / REPORT_LINK_SECRET / token

The token is printed once; only its SHA-256 hash is stored.
"""

from __future__ import annotations

import argparse
import secrets
import sys

from cryptography.fernet import Fernet
from sqlalchemy import select

from caseline.auth import ROLES, hash_token
from caseline.config import get_settings
from caseline.db import Database
from caseline.models import ApiPrincipal, Firm


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="caseline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("create-principal")
    cp.add_argument("--name", required=True)
    cp.add_argument("--role", required=True, choices=[r for r in ROLES if r != "service"])
    cp.add_argument("--firm", help="firm slug (required for firm_user)")
    dp = sub.add_parser("deactivate-principal")
    dp.add_argument("--name", required=True)
    sub.add_parser("generate-keys")
    sub.add_parser("seed", help="idempotently seed demo + fixture firms")
    args = parser.parse_args(argv)

    if args.cmd == "seed":
        from caseline.seed import seed

        settings = get_settings()
        with Database(settings.database_url).sessionmaker() as session:
            for f in seed(session, settings):
                print(f"{'DEMO' if f.is_demo else 'fixture':8} {f.slug:24} {f.display_name}")
        return 0

    if args.cmd == "generate-keys":
        print(f"CASELINE_FIELD_ENCRYPTION_KEY={Fernet.generate_key().decode()}")
        print(f"REPORT_LINK_SECRET={secrets.token_urlsafe(48)}")
        print(f"CASELINE_INTERNAL_API_TOKEN={secrets.token_urlsafe(48)}")
        return 0

    db = Database(get_settings().database_url)
    with db.sessionmaker() as session:
        if args.cmd == "deactivate-principal":
            row = session.scalar(select(ApiPrincipal).where(ApiPrincipal.name == args.name))
            if row is None:
                print("no such principal", file=sys.stderr)
                return 1
            row.active = False
            session.commit()
            print(f"deactivated {args.name}")
            return 0

        firm_id = None
        if args.role == "firm_user":
            if not args.firm:
                print("--firm is required for firm_user", file=sys.stderr)
                return 1
            firm = session.scalar(select(Firm).where(Firm.slug == args.firm))
            if firm is None:
                print(f"no firm {args.firm}", file=sys.stderr)
                return 1
            firm_id = firm.id
        token = secrets.token_urlsafe(32)
        session.add(ApiPrincipal(name=args.name, role=args.role, firm_id=firm_id, token_hash=hash_token(token)))
        session.commit()
        print(f"created {args.role} '{args.name}'. Token (shown once, store it in a password manager):")
        print(token)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
