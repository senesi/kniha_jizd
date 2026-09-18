"""deduplikace notifikaci podle doruceni, ne podle existence radku

Dosud stačilo, že řádek s `dedupe_key` vznikl, a připomínka se už nikdy
neposlala znovu. Když v tu chvíli odesílání pošty selhalo (nebo nebylo
vůbec nastavené), e-mail nedorazil **nikdy** — a nikdo se to nedozvěděl.

Nově je rozhodující `emailed_at`: dokud není vyplněné, smí se odeslání
při příštím běhu zopakovat.

Co tahle migrace přidává:

- `email_attempts` — kolikrát se odeslání zkusilo. Je to diagnostika,
  ne strop: připomínka se zkouší dál, dokud nedorazí, a tenhle sloupec
  říká, jak dlouho se to nedaří.
- částečný unikátní index na (`user_id`, `dedupe_key`) — dva souběžné
  běhy plánovače nesmí založit dvě stejné zprávy. Kontrola v aplikaci by
  mezi dotazem a zápisem měla okno, kterým oba projdou; index ho nemá.
  Platí jen pro řádky s klíčem, protože běžné notifikace (rezervace,
  závady) se opakovat smí a mají.

Data se nemění, jen přibývá sloupec s výchozí nulou.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "fleet"


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("email_attempts", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    # Dosud odeslané zprávy mají za sebou právě jeden pokus - ať už
    # dopadl jakkoliv. Bez toho by se tvářily, že se nikdy nezkoušely.
    op.execute(
        f"UPDATE {SCHEMA}.notifications SET email_attempts = 1 "
        f"WHERE emailed_at IS NOT NULL OR email_error IS NOT NULL"
    )

    op.create_index(
        "uq_fleet_notifications_dedupe",
        "notifications", ["user_id", "dedupe_key"],
        unique=True, schema=SCHEMA,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_fleet_notifications_dedupe", table_name="notifications", schema=SCHEMA)
    op.drop_column("notifications", "email_attempts", schema=SCHEMA)
