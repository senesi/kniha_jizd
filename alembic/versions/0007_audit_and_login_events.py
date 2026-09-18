"""audit: vozidlo, popis, vysledek + opravneni k prohlizeni

Audit v aplikaci existuje od Etapy 1 a zapisuje do něj třináct modulů.
Tahle migrace ho **nenahrazuje**, jen doplňuje, co chybělo k tomu, aby
šel použít jako administrátorská obrazovka:

- `vehicle_id` — dosud se vozidlo schovávalo uvnitř JSON payloadu, takže
  se podle něj nedalo filtrovat. Teď je to sloupec s indexem.
- `description` — stručný popis změny lidsky, vedle strojového JSONu.
- `result` — `success` / `failure`. Dává smysl hlavně u přihlášení;
  u běžné změny zůstává prázdný, protože neúspěšná změna se neuloží.
- oprávnění `core.audit.view` — jen pro administrátora.

Indexy odpovídají tomu, jak se bude filtrovat: čas (výchozí řazení),
uživatel, modul, akce, vozidlo.

Nic se nemaže ani nepřepisuje.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "core"
AUDIT_PERMISSION = "core.audit.view"


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("vehicle_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    # SET NULL, ne CASCADE: smazané vozidlo nesmí odnést historii toho, co
    # se s ním dělo. Audit je append-only právě proto.
    op.create_foreign_key(
        "fk_core_audit_log_vehicle", "audit_log", "vehicles",
        ["vehicle_id"], ["id"], source_schema=SCHEMA, referent_schema="fleet",
        ondelete="SET NULL",
    )
    op.add_column("audit_log", sa.Column("description", sa.String(length=255), nullable=True), schema=SCHEMA)
    op.add_column("audit_log", sa.Column("result", sa.String(length=20), nullable=True), schema=SCHEMA)

    for column in ("created_at", "user_id", "module", "action", "vehicle_id"):
        op.create_index(f"ix_core_audit_log_{column}", "audit_log", [column], schema=SCHEMA)

    # --- oprávnění k prohlížení auditu --------------------------------
    op.execute(
        f"INSERT INTO {SCHEMA}.permissions (code, description) "
        f"VALUES ('{AUDIT_PERMISSION}', 'Prohlížení auditního logu a přihlášení') "
        f"ON CONFLICT (code) DO NOTHING"
    )
    op.execute(
        f"INSERT INTO {SCHEMA}.role_permissions (role_id, permission_id) "
        f"SELECT r.id, p.id FROM {SCHEMA}.roles r, {SCHEMA}.permissions p "
        f"WHERE r.name = 'admin' AND p.code = '{AUDIT_PERMISSION}' "
        f"ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM {SCHEMA}.role_permissions WHERE permission_id IN "
        f"(SELECT id FROM {SCHEMA}.permissions WHERE code = '{AUDIT_PERMISSION}')"
    )
    op.execute(f"DELETE FROM {SCHEMA}.permissions WHERE code = '{AUDIT_PERMISSION}'")

    for column in ("created_at", "user_id", "module", "action", "vehicle_id"):
        op.drop_index(f"ix_core_audit_log_{column}", table_name="audit_log", schema=SCHEMA)

    op.drop_column("audit_log", "result", schema=SCHEMA)
    op.drop_column("audit_log", "description", schema=SCHEMA)
    op.drop_constraint("fk_core_audit_log_vehicle", "audit_log", schema=SCHEMA, type_="foreignkey")
    op.drop_column("audit_log", "vehicle_id", schema=SCHEMA)
