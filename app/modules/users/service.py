"""Uživatelé a jejich role (zadání 4).

Role jsou tři: admin, odpovedna_osoba, user. Rozsah „jen moje vozidla" u
odpovědné osoby nezajišťuje role, ale porovnání
fleet.vehicle.manage.own proti vehicle.responsible_user_id
(app/core/access.py) - role jen říká, že to oprávnění vůbec má.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import log_action
from app.core.security import hash_password, validate_password_policy, verify_password
from app.models.core import Role, User, UserRole

MODULE = "users"


class DuplicateEmail(Exception):
    pass


class UserError(Exception):
    pass


async def list_users(db: AsyncSession) -> list[User]:
    result = await db.execute(
        select(User).options(selectinload(User.roles).selectinload(UserRole.role)).order_by(User.full_name)
    )
    return list(result.scalars().all())


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles).selectinload(UserRole.role))
    )
    return result.scalar_one_or_none()


async def list_roles(db: AsyncSession) -> list[Role]:
    result = await db.execute(select(Role).order_by(Role.name))
    return list(result.scalars().all())


async def _email_taken(db: AsyncSession, email: str, *, exclude_id: uuid.UUID | None = None) -> bool:
    stmt = select(User.id).where(User.email == email)
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def create_user(
    db: AsyncSession, *, email: str, full_name: str, phone: str | None, password: str,
    role_names: list[str], actor_id: uuid.UUID,
) -> User:
    email = normalize_email(email)
    if not full_name.strip():
        raise UserError("Jméno je povinné.")
    if await _email_taken(db, email):
        raise DuplicateEmail(f"Uživatel s e-mailem {email} už existuje.")
    validate_password_policy(password)

    user = User(
        email=email, full_name=full_name.strip(), phone=phone or None, password_hash=hash_password(password),
    )
    db.add(user)
    await db.flush()
    await _set_roles(db, user, role_names)
    await log_action(
        db, user_id=actor_id, action="create", module=MODULE, entity_type="user", entity_id=str(user.id),
        after_data={"email": email, "full_name": user.full_name, "roles": sorted(role_names)},
    )
    await db.commit()
    return await get_user(db, user.id)


async def update_user(
    db: AsyncSession, user: User, *, email: str, full_name: str, phone: str | None, is_active: bool,
    role_names: list[str], actor_id: uuid.UUID,
) -> User:
    email = normalize_email(email)
    if not full_name.strip():
        raise UserError("Jméno je povinné.")
    if await _email_taken(db, email, exclude_id=user.id):
        raise DuplicateEmail(f"Uživatel s e-mailem {email} už existuje.")
    # Odebrat si vlastní admin roli nebo se deaktivovat = zamknout se ven.
    # Levnější je to nepustit než pak sahat do databáze ručně.
    if user.id == actor_id:
        if not is_active:
            raise UserError("Nemůžete deaktivovat sami sebe.")
        if "admin" not in role_names:
            raise UserError("Nemůžete si odebrat roli administrátora.")

    before = {
        "email": user.email, "full_name": user.full_name, "phone": user.phone, "is_active": user.is_active,
        "roles": sorted(assignment.role.name for assignment in user.roles),
    }
    user.email = email
    user.full_name = full_name.strip()
    user.phone = phone or None
    user.is_active = is_active
    await _set_roles(db, user, role_names)
    await log_action(
        db, user_id=actor_id, action="update", module=MODULE, entity_type="user", entity_id=str(user.id),
        before_data=before,
        after_data={"email": email, "full_name": user.full_name, "phone": user.phone,
                    "is_active": is_active, "roles": sorted(role_names)},
    )
    await db.commit()
    return await get_user(db, user.id)


async def _set_roles(db: AsyncSession, user: User, role_names: list[str]) -> None:
    roles = {role.name: role for role in await list_roles(db)}
    wanted = {name for name in role_names if name in roles}
    current = await db.execute(select(UserRole).where(UserRole.user_id == user.id))
    current_rows = list(current.scalars().all())
    current_names = set()
    for row in current_rows:
        name = next((n for n, role in roles.items() if role.id == row.role_id), None)
        if name in wanted:
            current_names.add(name)
        else:
            await db.delete(row)
    for name in wanted - current_names:
        db.add(UserRole(user_id=user.id, role_id=roles[name].id))
    await db.flush()


async def set_password(db: AsyncSession, user: User, new_password: str, actor_id: uuid.UUID) -> None:
    """Administrátorský reset. Staré heslo se nezadává - admin ho nezná a
    znát nemá; proto je to auditovaná akce."""
    validate_password_policy(new_password)
    user.password_hash = hash_password(new_password)
    await log_action(
        db, user_id=actor_id, action="password_reset", module=MODULE, entity_type="user", entity_id=str(user.id),
    )
    await db.commit()


async def change_own_password(db: AsyncSession, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        raise UserError("Současné heslo není správné.")
    validate_password_policy(new_password)
    user.password_hash = hash_password(new_password)
    await log_action(
        db, user_id=user.id, action="password_change", module=MODULE, entity_type="user", entity_id=str(user.id),
    )
    await db.commit()


async def update_own_profile(db: AsyncSession, user: User, *, full_name: str, phone: str | None) -> None:
    """E-mail je přihlašovací jméno - mění ho jen administrátor, ne
    uživatel sám."""
    if not full_name.strip():
        raise UserError("Jméno je povinné.")
    user.full_name = full_name.strip()
    user.phone = phone or None
    await log_action(
        db, user_id=user.id, action="update", module=MODULE, entity_type="user", entity_id=str(user.id),
        after_data={"full_name": user.full_name, "phone": user.phone},
    )
    await db.commit()
