"""vice typu u jednoho servisniho ukonu

Jedna návštěva servisu bývá víc úkonů najednou - vymění se olej, filtry
a k tomu se přehodí brzdové destičky. Dosud se musel vybrat jeden typ a
zbytek zůstal jen v popisu, takže se podle typu nedalo nic dohledat.

`service_type` (jedna hodnota) se proto mění na `service_types` (pole).
Data se převádějí, ne zahazují: každý existující záznam dostane pole s
právě tou hodnotou, kterou měl. Žádný záznam tím nepřijde o informaci a
downgrade vrací první prvek zpátky.

Prázdné pole nedává smysl - hlídá ho CHECK, ne jen aplikace.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "fleet"


def upgrade() -> None:
    op.add_column(
        "vehicle_services",
        sa.Column("service_types", postgresql.ARRAY(sa.String(length=30)), nullable=True),
        schema=SCHEMA,
    )

    # Převod dat: co bylo jednou hodnotou, je nadále jednoprvkové pole.
    op.execute(
        f"UPDATE {SCHEMA}.vehicle_services "
        f"SET service_types = ARRAY[COALESCE(service_type, 'jine')]::varchar(30)[]"
    )

    op.alter_column("vehicle_services", "service_types", nullable=False, schema=SCHEMA)
    op.create_check_constraint(
        "ck_fleet_vehicle_services_types_not_empty",
        "vehicle_services",
        "cardinality(service_types) > 0",
        schema=SCHEMA,
    )

    # Starý sloupec až nakonec - do té chvíle je z čeho převádět.
    op.drop_column("vehicle_services", "service_type", schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "vehicle_services",
        sa.Column("service_type", sa.String(length=30), nullable=True),
        schema=SCHEMA,
    )
    # Zpátky se vejde jen jeden typ - bere se první.
    op.execute(
        f"UPDATE {SCHEMA}.vehicle_services "
        f"SET service_type = COALESCE(service_types[1], 'jine')"
    )
    op.alter_column("vehicle_services", "service_type", nullable=False, schema=SCHEMA)

    op.drop_constraint(
        "ck_fleet_vehicle_services_types_not_empty", "vehicle_services",
        type_="check", schema=SCHEMA,
    )
    op.drop_column("vehicle_services", "service_types", schema=SCHEMA)
