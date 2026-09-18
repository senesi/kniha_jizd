"""individualni nastaveni notifikaci

Každý uživatel si sám rozhoduje, které typy notifikací chce dostávat.
Dosud o tom nerozhodoval nikdo - kdo byl odpovědnou osobou, dostával
všechno.

Tabulka drží **jen odchylky od výchozího nastavení**, ne řádek pro
každou kombinaci uživatel × typ. Chybějící řádek znamená „výchozí podle
katalogu" (app/core/notification_types.py), takže:

- stávající uživatelé po této migraci mají definované výchozí nastavení
  (u všech dnešních typů zapnuto), aniž by se cokoliv dopočítávalo,
- nový typ notifikace nepotřebuje migraci ani backfill,
- uživatel založený mimo aplikaci (import, skript) nezůstane bez řádků
  a nepřijde tím o notifikace.

Viz ROZHODNUTI.md R38.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "core"


def upgrade() -> None:
    op.create_table(
        "user_notification_preferences",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notification_type", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Jeden uživatel, jeden typ, jedna volba - dvě protichůdné
        # hodnoty pro totéž nesmí vzniknout ani při souběhu.
        sa.UniqueConstraint("user_id", "notification_type", name="uq_core_user_notification_pref"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_core_user_notification_preferences_user_id",
        "user_notification_preferences", ["user_id"], schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_core_user_notification_preferences_user_id",
        table_name="user_notification_preferences", schema=SCHEMA,
    )
    op.drop_table("user_notification_preferences", schema=SCHEMA)
