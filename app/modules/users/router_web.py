"""Správa uživatelů (admin) a vlastní účet (kdokoliv)."""
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import flash
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user, require_permission
from app.core.security import WeakPassword
from app.core.templates import render_page
from app.models.core import User
from app.modules.users import service

users_router = APIRouter(tags=["users-web"])
account_router = APIRouter(tags=["account-web"])

MANAGE = "core.user.manage"


async def _load(db: AsyncSession, user_id: uuid.UUID) -> User:
    target = await service.get_user(db, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Uživatel nebyl nalezen.")
    return target


def _role_names(form) -> list[str]:
    return [value for value in form.getlist("roles") if isinstance(value, str)]


# --- správa uživatelů -------------------------------------------------

@users_router.get("/users")
async def users_list(
    request: Request,
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(request, "users_list.html", user, db, users=await service.list_users(db))


@users_router.get("/users/new")
async def user_new_form(
    request: Request,
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(
        request, "user_form.html", user, db,
        target=None, roles=await service.list_roles(db), error=None, form={},
    )


@users_router.post("/users/new", dependencies=[Depends(verify_csrf)])
async def user_create(
    request: Request,
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    payload = {
        "email": str(form.get("email") or ""),
        "full_name": str(form.get("full_name") or ""),
        "phone": str(form.get("phone") or "").strip() or None,
        "roles": _role_names(form),
    }
    try:
        created = await service.create_user(
            db, email=payload["email"], full_name=payload["full_name"], phone=payload["phone"],
            password=str(form.get("password") or ""), role_names=payload["roles"], actor_id=user.id,
        )
    except (service.DuplicateEmail, service.UserError, WeakPassword) as exc:
        return await render_page(
            request, "user_form.html", user, db, status_code=400,
            target=None, roles=await service.list_roles(db), error=str(exc), form=payload,
        )
    return flash.redirect(f"/kniha-jizd/users/{created.id}", "user_created")


@users_router.get("/users/{user_id}")
async def user_detail(
    request: Request,
    user_id: uuid.UUID,
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(request, "user_detail.html", user, db, target=await _load(db, user_id))


@users_router.get("/users/{user_id}/edit")
async def user_edit_form(
    request: Request,
    user_id: uuid.UUID,
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(
        request, "user_form.html", user, db,
        target=await _load(db, user_id), roles=await service.list_roles(db), error=None, form={},
    )


@users_router.post("/users/{user_id}/edit", dependencies=[Depends(verify_csrf)])
async def user_update(
    request: Request,
    user_id: uuid.UUID,
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    target = await _load(db, user_id)
    form = await request.form()
    payload = {
        "email": str(form.get("email") or ""),
        "full_name": str(form.get("full_name") or ""),
        "phone": str(form.get("phone") or "").strip() or None,
        "roles": _role_names(form),
        "is_active": form.get("is_active") is not None,
    }
    try:
        await service.update_user(
            db, target, email=payload["email"], full_name=payload["full_name"], phone=payload["phone"],
            is_active=payload["is_active"], role_names=payload["roles"], actor_id=user.id,
        )
    except (service.DuplicateEmail, service.UserError) as exc:
        return await render_page(
            request, "user_form.html", user, db, status_code=400,
            target=target, roles=await service.list_roles(db), error=str(exc), form=payload,
        )
    return flash.redirect(f"/kniha-jizd/users/{target.id}", "user_updated")


@users_router.post("/users/{user_id}/password", dependencies=[Depends(verify_csrf)])
async def user_reset_password(
    user_id: uuid.UUID,
    new_password: str = Form(...),
    user: User = Depends(require_permission(MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    target = await _load(db, user_id)
    try:
        await service.set_password(db, target, new_password, user.id)
    except WeakPassword as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return flash.redirect(f"/kniha-jizd/users/{target.id}", "password_reset")


# --- vlastní účet ------------------------------------------------------

@account_router.get("/account/profile")
async def account_profile_form(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(request, "account_profile.html", user, db, error=None)


@account_router.post("/account/profile", dependencies=[Depends(verify_csrf)])
async def account_profile_save(
    request: Request,
    full_name: str = Form(...),
    phone: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await service.update_own_profile(db, user, full_name=full_name, phone=phone.strip() or None)
    except service.UserError as exc:
        return await render_page(request, "account_profile.html", user, db, status_code=400, error=str(exc))
    return flash.redirect("/kniha-jizd/account/profile", "profile_updated")


@account_router.get("/account/password")
async def account_password_form(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(request, "change_password.html", user, db, error=None)


@account_router.post("/account/password", dependencies=[Depends(verify_csrf)])
async def account_password_save(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_again: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if new_password != new_password_again:
        return await render_page(
            request, "change_password.html", user, db, status_code=400,
            error="Nová hesla se neshodují.",
        )
    try:
        await service.change_own_password(db, user, current_password, new_password)
    except (service.UserError, WeakPassword) as exc:
        return await render_page(request, "change_password.html", user, db, status_code=400, error=str(exc))
    return flash.redirect("/kniha-jizd/account/password", "password_changed")
