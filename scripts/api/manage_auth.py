"""Administrative CLI for the product authentication schema and first user."""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from db import get_connection

from api.auth_schema import PRODUCT_SCHEMA_VERSION, SCHEMA_VERSION, apply_schema, product_schema_is_ready, schema_is_ready
from api.services.auth_service import create_or_update_user


def _password_from_args(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    return getpass.getpass("Password: ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="Apply the idempotent product auth schema.")
    subparsers.add_parser("status", help="Check whether the auth schema is ready.")
    upsert = subparsers.add_parser("upsert-user", help="Create or rotate a local product user.")
    upsert.add_argument("--username", required=True)
    upsert.add_argument("--role", choices=("user", "admin"), default="user")
    upsert.add_argument("--password-stdin", action="store_true")
    upsert.add_argument("--no-force-password-change", action="store_true")
    args = parser.parse_args()

    if args.command == "migrate":
        connection = get_connection()
        try:
            apply_schema(connection)
        finally:
            connection.close()
        print(json.dumps({"status": "ok", "schemaVersion": SCHEMA_VERSION, "productSchemaVersion": PRODUCT_SCHEMA_VERSION}))
        return 0

    if args.command == "status":
        connection = get_connection()
        try:
            ready = schema_is_ready(connection)
            product_ready = product_schema_is_ready(connection) if ready else False
        finally:
            connection.close()
        print(
            json.dumps(
                {
                    "status": "ok" if ready and product_ready else "missing",
                    "schemaVersion": SCHEMA_VERSION,
                    "productSchemaVersion": PRODUCT_SCHEMA_VERSION,
                }
            )
        )
        return 0 if ready and product_ready else 1

    user = create_or_update_user(
        args.username,
        _password_from_args(args),
        role=args.role,
        force_password_change=not args.no_force_password_change,
    )
    print(json.dumps({"status": "ok", "id": user["id"], "username": user["username"], "role": user["role"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
