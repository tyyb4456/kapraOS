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
        # Check if user already exists
        existing_user = (await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))).scalar_one_or_none()
        if existing_user:
            print(f"[+] User with Clerk ID '{clerk_user_id}' already provisioned!")
            print(f"    User ID: {existing_user.id}")
            print(f"    Shop ID: {existing_user.shop_id}")
            print(f"    Role:    {existing_user.role}")
            return

        # Check if shop exists or create a new one
        existing_shop = (await db.execute(select(Shop).limit(1))).scalar_one_or_none()
        if not existing_shop:
            shop = Shop(
                name=shop_name,
                currency="PKR",
                address="Shop #14, Cloth Market, Faisalabad",
                phone="0300-8765432",
            )
            db.add(shop)
            await db.flush()
            print(f"[+] Created Shop: '{shop.name}' ({shop.id})")
        else:
            shop = existing_shop
            print(f"[+] Using existing Shop: '{shop.name}' ({shop.id})")

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
        print("Usage: python -m app.scripts.seed_dev_user <clerk_user_id> [name] [email] [shop_name]")
        sys.exit(1)

    clerk_id = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else "Tayyab Hussain"
    email = sys.argv[3] if len(sys.argv) > 3 else "owner@kapraos.local"
    shop_name = sys.argv[4] if len(sys.argv) > 4 else "KapraOS Fabrics & Suiting"

    asyncio.run(seed_user(clerk_id, name, email, shop_name))


if __name__ == "__main__":
    main()
