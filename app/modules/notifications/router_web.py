"""Upozornění v aplikaci (zadání 20).

E-mail je jen doručovací kanál navíc - seznam tady je úplný i tehdy, když
SMTP není nakonfigurované nebo selhalo (viz app/core/mailer.py).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import flash
from app.core.csrf import verify_csrf
from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.templates import render_page
from app.models.core import User
from app.modules.notifications import repository, service

router = APIRouter(tags=["notifications-web"])


@router.get("/notifications")
async def notifications_list(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await render_page(
        request, "notifications.html", user, db,
        notifications=await repository.list_for_user(db, user.id),
    )


@router.post("/notifications/read-all", dependencies=[Depends(verify_csrf)])
async def notifications_read_all(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.mark_all_read(db, user.id)
    return flash.redirect("/kniha-jizd/notifications", "notifications_read")


@router.get("/notifications/{notification_id}")
async def notification_open(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Otevření upozornění = jeho přečtení. Cizí upozornění je 404, ne
    403 - komu co přišlo, není nikomu jinému nic do toho (zadání 30, IDOR)."""
    notification = await repository.get(db, notification_id)
    if notification is None or notification.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upozornění nebylo nalezeno.")
    await service.mark_read(db, notification)
    return flash.redirect(notification.link_url or "/kniha-jizd/notifications")
