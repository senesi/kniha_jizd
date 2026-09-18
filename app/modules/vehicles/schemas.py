import uuid
from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.fleet import (
    FUEL_TYPES,
    VEHICLE_SCOPES,
    VEHICLE_STATUSES,
    VEHICLE_TYPES,
    VEHICLE_VISIBILITIES,
)


class VehicleBase(BaseModel):
    internal_code: str = Field(min_length=1, max_length=50)
    license_plate: str = Field(min_length=1, max_length=20)
    brand: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    vin: str | None = Field(default=None, max_length=32)
    year_of_manufacture: int | None = Field(default=None, ge=1900, le=2100)
    vehicle_type: str = "osobni"
    fuel_type: str | None = None
    tank_capacity_l: float | None = Field(default=None, gt=0, le=2000)
    battery_capacity_kwh: float | None = Field(default=None, gt=0, le=1000)
    responsible_user_id: uuid.UUID | None = None
    status: str = "available"
    is_active: bool = True
    approval_required: bool = False
    visibility: str = "all"
    # Firemní, nebo soukromé vozidlo jednoho uživatele. Výchozí "company"
    # drží dosavadní chování: kdo pole neposílá, zakládá firemní vozidlo.
    vehicle_scope: str = "company"
    owner_user_id: uuid.UUID | None = None
    stk_valid_until: date | None = None
    vignette_valid_until: date | None = None
    insurance_company: str | None = Field(default=None, max_length=255)
    insurance_policy_number: str | None = Field(default=None, max_length=100)
    insurance_valid_until: date | None = None
    last_oil_change_at: date | None = None
    last_oil_change_km: int | None = Field(default=None, ge=0)
    oil_interval_km: int | None = Field(default=None, gt=0)
    oil_interval_months: int | None = Field(default=None, gt=0)
    notes: str | None = None

    @field_validator("vehicle_type")
    @classmethod
    def _valid_type(cls, value: str) -> str:
        if value not in VEHICLE_TYPES:
            raise ValueError(f"Neplatný typ vozidla: {value}")
        return value

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        if value not in VEHICLE_STATUSES:
            raise ValueError(f"Neplatný stav vozidla: {value}")
        return value

    @field_validator("visibility")
    @classmethod
    def _valid_visibility(cls, value: str) -> str:
        if value not in VEHICLE_VISIBILITIES:
            raise ValueError(f"Neplatná viditelnost vozidla: {value}")
        return value

    @field_validator("vehicle_scope")
    @classmethod
    def _valid_scope(cls, value: str) -> str:
        if value not in VEHICLE_SCOPES:
            raise ValueError(f"Neplatný rozsah vozidla: {value}")
        return value

    @model_validator(mode="after")
    def _owner_matches_scope(self):
        """Soukromé vozidlo má vlastníka, firemní ne.

        Totéž hlídá CHECK v migraci 0008; tady je to proto, aby uživatel
        dostal srozumitelnou hlášku místo chyby z databáze. Firemnímu
        vozidlu se vlastník tiše odebere - přepnutí rozsahu ve formuláři
        nemá padat na hodnotě, kterou uživatel nevidí."""
        if self.vehicle_scope == "private":
            if self.owner_user_id is None:
                raise ValueError("U soukromého vozidla je potřeba vlastník.")
            # Odpovědná osoba je firemní role a u soukromého vozidla nedává
            # smysl - viz app/core/access.py.
            self.responsible_user_id = None
        else:
            self.owner_user_id = None
        return self

    @field_validator("fuel_type")
    @classmethod
    def _valid_fuel(cls, value: str | None) -> str | None:
        if value and value not in FUEL_TYPES:
            raise ValueError(f"Neplatný typ paliva: {value}")
        return value or None

    @field_validator("license_plate")
    @classmethod
    def _normalize_plate(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("vin")
    @classmethod
    def _normalize_vin(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None

    @field_validator("insurance_company", "insurance_policy_number")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """An untouched text input posts "" - stored as NULL, so "není
        zadáno" is one state in the database, not two."""
        return value.strip() or None if value else None


class VehicleCreate(VehicleBase):
    # Only ever the starting point - from here on the odometer is driven by
    # closed trips or an explicit administrative correction, never by
    # editing this field again (see VehicleUpdate, which omits it).
    current_odometer_km: int = Field(default=0, ge=0)
    current_fuel_level: int | None = Field(default=None, ge=0, le=100)


class VehicleUpdate(VehicleBase):
    """Deliberately without current_odometer_km: a plain edit must not be
    able to move the odometer (zadání 5 - it may never go backwards without
    an explicit administrative step). That step is its own route/permission
    - see vehicles/service.py:correct_odometer."""
