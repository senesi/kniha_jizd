import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.core import Permission, Role, RolePermission, User, UserRole


async def _load_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    return result.scalar_one_or_none()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    user_id: str | None = request.session.get("user_id") if hasattr(request, "session") else None
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = await _load_user(db, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


async def get_user_permission_codes(db: AsyncSession, user_id: uuid.UUID) -> set[str]:
    result = await db.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    return {row[0] for row in result.all()}


async def get_user_role_names(db: AsyncSession, user_id: uuid.UUID) -> set[str]:
    result = await db.execute(
        select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
    )
    return {row[0] for row in result.all()}


def require_permission(code: str):
    async def _check(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        codes = await get_user_permission_codes(db, user.id)
        if code not in codes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nemáš oprávnění k této akci.")
        return user

    return _check


def require_any_permission(*codes: str):
    """Baseline gate for a route reachable through more than one permission
    - typically fleet.vehicle.manage (admin, any vehicle) OR
    fleet.vehicle.manage.own (the vehicle's responsible person). It only
    rules out actors holding NEITHER code; the per-vehicle decision (is
    THIS vehicle's responsible_user_id THIS actor) still has to happen in
    the route body once the vehicle is loaded - see
    app/core/access.py:assert_vehicle_manage_access, which this dependency
    cannot do in advance because it runs before anything is fetched."""

    async def _check(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        user_codes = await get_user_permission_codes(db, user.id)
        if not (set(codes) & user_codes):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nemáš oprávnění k této akci.")
        return user

    return _check
