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


async def claim_for_delivery(
    db: AsyncSession, user_id: uuid.UUID, dedupe_key: str,
) -> tuple[Notification | None, bool]:
    """Zamluví si zprávu s tímhle klíčem pro doručení.

    Vrací `(řádek, obsazeno)`:

    - `(None, False)` — žádný takový řádek zatím není, volající ho smí
      založit;
    - `(řádek, False)` — řádek je náš až do konce transakce; podle
      `emailed_at` se pozná, jestli se má odeslání zkusit, nebo je
      hotovo;
    - `(None, True)` — řádek existuje, ale právě ho drží jiný souběžný
      běh. Volající nesmí dělat nic: druhý běh to dokončí a dvě stejné
      zprávy by jinak odešly obě.

    `FOR UPDATE SKIP LOCKED` je tu proto, že samotný unikátní index
    ochrání jen zakládání. Opakované odeslání už existující zprávy by
    přes něj prošlo dvakrát — dva běhy by našly `emailed_at IS NULL` a
    oba poslaly e-mail. Zámek drží po dobu odesílání ten, kdo ho získal;
    druhý běh místo čekání prostě přeskočí (SKIP LOCKED), protože
    připomínku už stejně někdo vyřizuje."""
    locked = (await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id, Notification.dedupe_key == dedupe_key)
        .with_for_update(skip_locked=True)
    )).scalar_one_or_none()
    if locked is not None:
        return locked, False

    # Nic jsme nezamkli. Buď řádek neexistuje, nebo ho drží někdo jiný -
    # a to je rozdíl, který se musí poznat.
    exists = (await db.execute(
        select(Notification.id).where(
            Notification.user_id == user_id, Notification.dedupe_key == dedupe_key
        )
    )).scalar_one_or_none() is not None
    return None, exists
