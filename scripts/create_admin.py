"""Jednorázový bootstrap: první administrátor.

Použití (v běžícím kontejneru aplikace, nebo lokálně s načteným .env):
    ADMIN_EMAIL=vy@example.com ADMIN_FULL_NAME="Jméno Příjmení" python -m scripts.create_admin

Bez ADMIN_PASSWORD se vygeneruje náhodné heslo a jednou se vypíše na
stdout. Přesměrujte výstup do souboru s omezenými právy, nevkládejte ho
do chatu ani do logu.

Role a oprávnění vytváří migrace 0001, tady se už jen přiřazují.
"""
import asyncio
import os
import secrets

from sqlalchemy import select

from app.core.db import async_session_factory
from app.core.security import hash_password
from app.models.core import Role, User, UserRole


async def main() -> None:
    email = os.environ["ADMIN_EMAIL"].strip().lower()
    full_name = os.environ.get("ADMIN_FULL_NAME", "Administrátor")
    password = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(18)

    async with async_session_factory() as db:
        existing = await db.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            print(f"Uživatel {email} už existuje, nic se nemění.")
            return

        admin_role = (await db.execute(select(Role).where(Role.name == "admin"))).scalar_one()

        user = User(email=email, full_name=full_name, password_hash=hash_password(password))
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=user.id, role_id=admin_role.id))
        await db.commit()

        print(f"Založen administrátor: {email}")
        if "ADMIN_PASSWORD" not in os.environ:
            print(f"Vygenerované heslo: {password}")


if __name__ == "__main__":
    asyncio.run(main())
