"""Platform-admin bootstrap (operator-run, database-level).

KapraOS intentionally has **no HTTP endpoint that grants platform-admin
access**: a public admin-registration route (or an "admin" value the
client could send) would be a privilege-escalation hole. The initial
administrator - and any later change - is established by an operator with
direct database access running this script, which is consistent with the
project's deployment model (database access is already required to run
migrations and the provisioning scripts).

It never creates accounts and never changes any other field: it only
flips ``users.is_platform_admin`` for an **already-provisioned** user,
identified by Clerk user id or email. Every change is recorded in
``admin_audit_logs`` in the same transaction. Revoking the last remaining
platform administrator is refused.

Usage (from the ``backend`` directory, same environment as migrations):

    # Grant access
    python -m app.scripts.grant_platform_admin user_2abc123
    python -m app.scripts.grant_platform_admin owner@example.com

    # Revoke access
    python -m app.scripts.grant_platform_admin owner@example.com --revoke

    # Inspect current administrators
    python -m app.scripts.grant_platform_admin --list

    # Attribute the change to a named operator in the audit log
    python -m app.scripts.grant_platform_admin owner@example.com --actor "ops-runbook-42"

After granting, the user signs in normally; ``GET /auth/me`` will report
``is_platform_admin: true`` and the frontend reveals the admin section.
All admin APIs re-verify the flag server-side on every request.
"""

import argparse
import asyncio
import sys

from app.database.session import AsyncSessionLocal
from app.services.admin import (
    LastPlatformAdminError,
    PlatformAdminTargetNotFoundError,
    list_platform_admins,
    set_platform_admin,
)


async def grant_or_revoke(
    identifier: str, *, grant: bool, actor: str | None = None
) -> int:
    """Apply one grant/revoke and report the outcome. Returns an exit code."""
    async with AsyncSessionLocal() as db:
        try:
            user = await set_platform_admin(
                db,
                identifier=identifier,
                grant=grant,
                actor_label=actor,
            )
        except PlatformAdminTargetNotFoundError as exc:
            print(f"[!] {exc}")
            print(
                "    The user must sign up (or be provisioned) before the "
                "platform-admin flag can be granted."
            )
            return 1
        except LastPlatformAdminError as exc:
            print(f"[!] {exc}")
            return 1

        # The flag write and its audit entry commit together; a failure
        # leaves both untouched.
        await db.commit()

    verb = "granted to" if grant else "revoked from"
    print(f"[+] Platform-admin access {verb} '{user.name}' <{user.email}>")
    print(f"    User ID:      {user.id}")
    print(f"    Clerk User ID: {user.clerk_user_id}")
    print(f"    Audit log:    admin_audit_logs ({'granted' if grant else 'revoked'})")
    return 0


async def show_admins() -> int:
    """Print every current platform administrator."""
    async with AsyncSessionLocal() as db:
        admins = await list_platform_admins(db)

    if not admins:
        print("[i] No platform administrators are configured.")
        return 0

    print(f"[i] {len(admins)} platform administrator(s):")
    for admin in admins:
        print(
            f"    - {admin.name} <{admin.email}> "
            f"(clerk: {admin.clerk_user_id}, id: {admin.id})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.scripts.grant_platform_admin",
        description=(
            "Grant/revoke KapraOS platform-admin access for an existing user "
            "(identified by Clerk user id or email)."
        ),
    )
    parser.add_argument(
        "identifier",
        nargs="?",
        help="Clerk user id (user_...) or account email of the target user.",
    )
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="Revoke platform-admin access instead of granting it.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List current platform administrators and exit.",
    )
    parser.add_argument(
        "--actor",
        default=None,
        help="Free-form operator label recorded in the audit log.",
    )

    args = parser.parse_args(argv)

    if args.list:
        return asyncio.run(show_admins())

    if not args.identifier:
        parser.error("identifier is required unless --list is used")

    return asyncio.run(
        grant_or_revoke(
            args.identifier,
            grant=not args.revoke,
            actor=args.actor,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
