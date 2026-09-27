"""Seed script to provision a Shop and link a Clerk User in the database.

Usage:
    python -m app.scripts.seed_dev_user <clerk_user_id> [name] [email] [shop_name]
    
Example:
    python -m app.scripts.seed_dev_user user_2abc123 "Tayyab Hussain" "tayyab@example.com" "KapraOS Fabric Store"
"""

import sys
import asyncio
from sqlalchemy import select
from app.database.session import AsyncSessionLocal
from app.models.shop import Shop
from app.models.user import User, UserRole


async def seed_user(clerk_user_id: str, name: str = "Store Owner", email: str = "owner@kapraos.local", shop_name: str = "KapraOS Fabrics & Suiting"):
    async with AsyncSessionLocal() as db:
        # Check if user already exists (idempotent re-run)
        existing_user = (await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))).scalar_one_or_none()
        if existing_user:
            print(f"[+] User with Clerk ID '{clerk_user_id}' already provisioned!")
            print(f"    User ID: {existing_user.id}")
            print(f"    Shop ID: {existing_user.shop_id}")
            print(f"    Role:    {existing_user.role}")
            from app.services.accounting import ensure_system_accounts
            await ensure_system_accounts(db, shop_id=existing_user.shop_id)
            await db.commit()
            return

        # Duplicate-email guard: same email, different Clerk identity.
        existing_email = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing_email is not None:
            print(f"[!] Account already exists for email '{email}' (Clerk ID '{existing_email.clerk_user_id}').")
            print(f"    Kindly sign in instead -- refusing to provision a duplicate tenant.")
            return

        # Each Clerk identity gets its own isolated Shop.
        # Never reuse an existing Shop here -- that was the bug that made
        # every account see the same data.
        shop = Shop(
            name=shop_name,
            currency="PKR",
        )
        db.add(shop)
        await db.flush()
        print(f"[+] Created Shop: '{shop.name}' ({shop.id})")

        # Initialize chart of accounts
        from app.services.accounting import ensure_system_accounts
        await ensure_system_accounts(db, shop_id=shop.id)

        # Create user
        user = User(
            shop_id=shop.id,
            clerk_user_id=clerk_user_id,
            name=name,
            email=email,
            role=UserRole.OWNER,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        print(f"[+] Successfully provisioned user '{user.name}' as OWNER!")
        print(f"    User ID:       {user.id}")
        print(f"    Clerk User ID: {user.clerk_user_id}")
        print(f"    Shop ID:       {user.shop_id}")


def main():
    if len(sys.argv) < 2:
        # Try auto-detecting user from Clerk
        try:
            from clerk_backend_api import Clerk
            from app.config import get_settings
            settings = get_settings()
            if settings.clerk_secret_key:
                clerk = Clerk(bearer_auth=settings.clerk_secret_key)
                users = clerk.users.list()
                if users:
                    u = users[0]
                    clerk_id = u.id
                    name = f"{u.first_name or ''} {u.last_name or ''}".strip() or "Tayyab Hussain"
                    email = u.email_addresses[0].email_address if u.email_addresses else "owner@kapraos.local"
                    print(f"[*] Auto-detected Clerk user: {name} ({email}, ID: {clerk_id})")
                    asyncio.run(seed_user(clerk_id, name, email, "KapraOS Fabrics & Suiting"))
                    return
        except Exception as exc:
            print(f"[!] Auto-detect failed: {exc}")

        print("Usage: python -m app.scripts.seed_dev_user <clerk_user_id> [name] [email] [shop_name]")
        sys.exit(1)

    clerk_id = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else "Tayyab Hussain"
    email = sys.argv[3] if len(sys.argv) > 3 else "owner@kapraos.local"
    shop_name = sys.argv[4] if len(sys.argv) > 4 else "KapraOS Fabrics & Suiting"

    asyncio.run(seed_user(clerk_id, name, email, shop_name))


if __name__ == "__main__":
    main()
