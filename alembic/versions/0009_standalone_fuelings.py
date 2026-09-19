"""tankovani i bez jizdy

U části vozidel se kniha jízd nevede a eviduje se jen tankování a
servis; z těch se počítá průměrná spotřeba (datum, stav tachometru,
objem). Elektromobil nabíjený přes noc v depu navíc žádnou jízdu nemá
vůbec, takže se jeho nabíjení dosud nedalo zapsat.

`trip_fuelings.trip_id` proto přestává být povinné. Tabulka i model si
jméno nechávají: přejmenování by si vyžádalo migraci dat bez jakéhokoliv
přínosu a `vehicle_id` v ní je denormalizované od začátku, takže data
byla vždycky vozidlová.

Zpětná kompatibilita: existující řádky mají `trip_id` vyplněné a nemění
se. Uvolnění NOT NULL nemůže žádný dosavadní záznam rozbít.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "fleet"


def upgrade() -> None:
    op.alter_column(
        "trip_fuelings", "trip_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True, schema=SCHEMA,
    )


def downgrade() -> None:
    # Zpátky se vejdou jen tankování navázaná na jízdu. Samostatná by
    # NOT NULL neprošla, takže se musí odstranit - a to je ztráta dat,
    # na kterou downgrade upozorní tím, že ji udělá viditelně.
    op.execute(f"DELETE FROM {SCHEMA}.trip_fuelings WHERE trip_id IS NULL")
    op.alter_column(
        "trip_fuelings", "trip_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False, schema=SCHEMA,
    )
