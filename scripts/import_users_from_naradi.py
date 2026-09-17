"""Jednorázový import uživatelů z Evidence nářadí do Knihy jízd.

Obě aplikace hashují hesla stejně (argon2 přes `PasswordHasher()` s
výchozími parametry, viz `app/core/security.py` v obou projektech), takže
`password_hash` se kopíruje **tak jak je** - nic se nepřehashovává a
uživatelé se přihlásí svým dosavadním heslem.

Databáze zůstávají oddělené. Tohle je jednorázová kopie řádků, ne
sdílená tabulka: po importu si každá aplikace žije vlastním životem a
změna hesla v jedné se do druhé nepropíše.

Postup je dvoukrokový a schválně **nepropojuje sítě** obou projektů -
kontejnery Knihy jízd a Evidence nářadí jsou v oddělených Docker sítích a
to má zůstat. Mezi nimi cestuje jen exportovaný soubor.

1) export ze zdrojové databáze (na VPS, čte jen Evidence nářadí):

    sudo docker exec naradi-postgres psql -U postgres -d naradi -t -A -c \
      "select json_agg(t) from (select u.email, u.full_name, u.phone,
         u.password_hash, u.is_active,
         coalesce(array_agg(r.name) filter (where r.name is not null), '{}') as roles
       from core.users u
       left join core.user_roles ur on ur.user_id = u.id
       left join core.roles r on r.id = ur.role_id
       group by u.id, u.email, u.full_name, u.phone, u.password_hash,
                u.is_active) t" > /tmp/naradi_users.json

2) import do Knihy jízd:

    sudo docker cp /tmp/naradi_users.json kniha-jizd-app:/tmp/users.json
    sudo docker exec kniha-jizd-app python /app/scripts/import_users_from_naradi.py /tmp/users.json [--apply]

Bez `--apply` jen vypíše, co by udělal (dry run).

Exportovaný soubor obsahuje hashe hesel - po importu ho smaž
(`rm /tmp/naradi_users.json` na hostu i v kontejneru).

Vlastnosti:
  - **nikdy nepřepíše** existující účet v Knize jízd - existující e-mail
    se přeskočí, takže opakované spuštění nic nerozbije
  - služební/bootstrap účty lze vynechat přes SKIP_EMAIL_DOMAINS
  - role se mapují podle ROLE_MAP; neznámá role skončí jako řidič
  - hesla se nikdy nevypisují ani nelogují
"""
import argparse
import asyncio
import json
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.db import async_session_factory
from app.models.core import Role, User, UserRole

# Role Evidence nářadí -> role Knihy jízd. Obě aplikace mají shodou
# okolností stejná jména pro admin i user, ale mapování je explicitní -
# aby se při přidání role v jednom projektu nezměnilo chování druhého.
ROLE_MAP = {
    "admin": "admin",
    "user": "user",
}
DEFAULT_ROLE = "user"

# Bootstrap/služební účty Evidence nářadí. V Knize jízd nemají co dělat -
# vlastního administrátora už má z nasazení.
SKIP_EMAIL_DOMAINS = {"naradi.solareg.azunimb.cz"}


def read_source(path: str) -> list[dict]:
    """Přečte exportovaný JSON. Žádné spojení do cizí databáze - viz
    docstring modulu."""
    with open(path, encoding="utf-8") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list):
        raise ValueError("export musí být JSON seznam")
    for row in rows:
        for field in ("email", "full_name", "password_hash", "is_active"):
            if field not in row:
                raise ValueError(f"v exportu chybí pole {field!r}")
    return sorted(rows, key=lambda row: row.get("full_name") or "")


def target_role(source_roles: list[str]) -> str:
    """Nejvyšší odpovídající role. Admin vyhrává, jinak řidič."""
    mapped = {ROLE_MAP.get(name, DEFAULT_ROLE) for name in source_roles}
    return "admin" if "admin" in mapped else DEFAULT_ROLE


async def import_users(db: AsyncSession, candidates: list[dict], *, apply: bool) -> None:
    roles = {role.name: role for role in (await db.execute(select(Role))).scalars().all()}
    existing = {
        email for (email,) in (await db.execute(select(User.email))).all()
    }

    created = skipped_existing = skipped_service = 0

    for row in candidates:
        email = row["email"].strip().lower()
        domain = email.split("@")[-1]

        if domain in SKIP_EMAIL_DOMAINS:
            print(f"  – {row['full_name']}: přeskočeno (služební účet)")
            skipped_service += 1
            continue

        if email in existing:
            print(f"  – {row['full_name']}: přeskočeno (v Knize jízd už je)")
            skipped_existing += 1
            continue

        role_name = target_role(list(row["roles"]))
        print(f"  + {row['full_name']}: založit jako {role_name}")
        created += 1

        if not apply:
            continue

        user = User(
            email=email,
            full_name=row["full_name"],
            phone=row["phone"],
            # Kopie hashe, ne nové heslo - uživatel se přihlásí tím, co zná.
            password_hash=row["password_hash"],
            is_active=row["is_active"],
        )
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=user.id, role_id=roles[role_name].id))
        await log_action(
            db, user_id=None, action="import", module="users", entity_type="user",
            entity_id=str(user.id),
            after_data={"email": email, "role": role_name, "source": "evidence_naradi"},
        )

    if apply:
        await db.commit()

    print()
    print(f"založeno: {created}, přeskočeno (už existují): {skipped_existing}, "
          f"přeskočeno (služební): {skipped_service}")
    if not apply:
        print()
        print("TOHLE BYL JEN NÁHLED. Pro skutečný import přidej --apply.")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", help="cesta k JSON exportu z Evidence nářadí")
    parser.add_argument("--apply", action="store_true", help="skutečně zapsat (bez toho jen náhled)")
    args = parser.parse_args()

    if not os.path.isfile(args.export):
        print(f"CHYBA: {args.export} neexistuje.", file=sys.stderr)
        sys.exit(1)

    candidates = read_source(args.export)
    print(f"v exportu: {len(candidates)} uživatelů")
    print()

    async with async_session_factory() as db:
        await import_users(db, candidates, apply=args.apply)


if __name__ == "__main__":
    asyncio.run(main())
