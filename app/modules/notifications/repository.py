import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fleet import Notification


async def count_unread(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id, Notification.read_at.is_(None)
        )
    )
    return int(result.scalar_one())


async def list_for_user(db: AsyncSession, user_id: uuid.UUID, *, limit: int = 100) -> list[Notification]:
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id)
        .options(selectinload(Notification.vehicle))
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get(db: AsyncSession, notification_id: uuid.UUID) -> Notification | None:
    result = await db.execute(select(Notification).where(Notification.id == notification_id))
    return result.scalar_one_or_none()


async def dedupe_key_exists(db: AsyncSession, user_id: uuid.UUID, dedupe_key: str) -> bool:
    result = await db.execute(
        select(Notification.id).where(
            Notification.user_id == user_id, Notification.dedupe_key == dedupe_key
        )
    )
    return result.scalar_one_or_none() is not None
