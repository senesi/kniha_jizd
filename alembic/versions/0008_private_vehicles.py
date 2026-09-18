"""soukroma vozidla uzivatelu

Vozidlo dostává rozsah: `company` (firemní, dosavadní stav) nebo
`private` (soukromé vozidlo konkrétního uživatele uvnitř téže instalace).

**Není to multi-tenancy.** Jedna instalace = jedna organizace; soukromé
vozidlo je jen vozidlo, které patří jednomu člověku a firmy se netýká.
Žádné tenant_id, žádný druhý model vozidla.

Zpětná kompatibilita:

- `vehicle_scope` má server_default 'company', takže **všechna stávající
  vozidla zůstávají firemní** a nedostanou vlastníka,
- `owner_user_id` je prázdný u všech dosavadních řádků,
- CHECK hlídá, že soukromé vozidlo vlastníka má a firemní ho nemá -
  jinak by „soukromé vozidlo nikoho" nebylo vidět pro nikoho.

Přibývá oprávnění `fleet.vehicle.private` pro všechny role: každý
uživatel smí mít vlastní soukromé vozidlo. Rozhodnutí o konkrétním
vozidle dělá porovnání `owner_user_id` proti přihlášenému, ne role -
stejně jako u `fleet.vehicle.manage.own` (app/core/access.py).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "fleet"
CORE_SCHEMA = "core"
PRIVATE_PERMISSION = "fleet.vehicle.private"


def upgrade() -> None:
    op.add_column(
        "vehicles",
        sa.Column("vehicle_scope", sa.String(length=20), nullable=False, server_default="company"),
        schema=SCHEMA,
    )
    op.add_column(
        "vehicles",
        sa.Column("owner_user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    # RESTRICT, ne CASCADE: smazání uživatele nesmí tiše odnést jeho
    # vozidlo i s celou historií jízd. Účty se stejně deaktivují, nemažou.
    op.create_foreign_key(
        "fk_fleet_vehicles_owner", "vehicles", "users",
        ["owner_user_id"], ["id"], source_schema=SCHEMA, referent_schema=CORE_SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_fleet_vehicles_owner_user_id", "vehicles", ["owner_user_id"], schema=SCHEMA,
    )
    op.create_index(
        "ix_fleet_vehicles_scope", "vehicles", ["vehicle_scope"], schema=SCHEMA,
    )

    op.create_check_constraint(
        "ck_fleet_vehicles_scope", "vehicles",
        "vehicle_scope IN ('company', 'private')", schema=SCHEMA,
    )
    # Soukromé vozidlo bez vlastníka by neviděl nikdo; firemní vozidlo s
    # vlastníkem by bylo dvojznačné. Hlídá to databáze, ne jen aplikace.
    op.create_check_constraint(
        "ck_fleet_vehicles_owner_matches_scope", "vehicles",
        "(vehicle_scope = 'private' AND owner_user_id IS NOT NULL) "
        "OR (vehicle_scope = 'company' AND owner_user_id IS NULL)",
        schema=SCHEMA,
    )

    # --- oprávnění pro všechny role ----------------------------------
    op.execute(
        f"INSERT INTO {CORE_SCHEMA}.permissions (code, description) "
        f"VALUES ('{PRIVATE_PERMISSION}', 'Vlastní soukromá vozidla') "
        f"ON CONFLICT (code) DO NOTHING"
    )
    op.execute(
        f"INSERT INTO {CORE_SCHEMA}.role_permissions (role_id, permission_id) "
        f"SELECT r.id, p.id FROM {CORE_SCHEMA}.roles r, {CORE_SCHEMA}.permissions p "
        f"WHERE p.code = '{PRIVATE_PERMISSION}' "
        f"ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM {CORE_SCHEMA}.role_permissions WHERE permission_id IN "
        f"(SELECT id FROM {CORE_SCHEMA}.permissions WHERE code = '{PRIVATE_PERMISSION}')"
    )
    op.execute(f"DELETE FROM {CORE_SCHEMA}.permissions WHERE code = '{PRIVATE_PERMISSION}'")

    op.drop_constraint("ck_fleet_vehicles_owner_matches_scope", "vehicles", schema=SCHEMA, type_="check")
    op.drop_constraint("ck_fleet_vehicles_scope", "vehicles", schema=SCHEMA, type_="check")
    op.drop_index("ix_fleet_vehicles_scope", table_name="vehicles", schema=SCHEMA)
    op.drop_index("ix_fleet_vehicles_owner_user_id", table_name="vehicles", schema=SCHEMA)
    op.drop_constraint("fk_fleet_vehicles_owner", "vehicles", schema=SCHEMA, type_="foreignkey")
    op.drop_column("vehicles", "owner_user_id", schema=SCHEMA)
    op.drop_column("vehicles", "vehicle_scope", schema=SCHEMA)
