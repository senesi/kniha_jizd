"""Vehicle-side data model (schema `fleet`).

One module for the whole vehicle domain - vehicle, its documents, service
history, defects, reservations, trips, fuelings, attachments and
notifications. They are a single tightly-connected graph (everything hangs
off Vehicle), so splitting them across files would only add cross-imports
without buying any isolation.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    and_,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

SCHEMA = "fleet"
CORE_SCHEMA = "core"

# Operational state. "Vypůjčené" is deliberately NOT one of these - whether
# a vehicle is out right now is derived from an open Trip
# (trips.repository.get_active_trip_for_vehicle), never a stored flag that
# could go stale. Same reasoning as Evidence nářadí's reservation handling.
VEHICLE_STATUSES = ["available", "in_service", "blocked"]
VEHICLE_TYPES = ["osobni", "dodavka", "nakladni", "pracovni_stroj", "prives", "jine"]
FUEL_TYPES = ["nafta", "benzin", "elektro", "hybrid", "lpg", "cng", "jine"]

TRIP_STATUSES = ["active", "completed", "cancelled"]
# Předdefinované účely jízdy (zadání 13) + volný text v purpose_text.
TRIP_PURPOSES = ["servis", "montaz", "doprava_materialu", "schuzka", "sluzebni_cesta", "jine"]

# Kdo vozidlo uvidí (požadavek D). "restricted" = jen odpovědná osoba a
# administrátor; pro ostatní vozidlo neexistuje nikde - v seznamu, na
# přehledu, ve výběru, v kalendáři ani přes QR.
VEHICLE_VISIBILITIES = ["all", "restricted"]

# Žádost o použití vozidla u vozidel s approval_required (požadavek B).
# "expired" je jen čtená vlastnost, ne uložený stav - viz TripRequest.
TRIP_REQUEST_STATUSES = ["pending", "approved", "rejected", "cancelled"]
TRIP_REQUEST_TERMINAL_STATUSES = {"rejected", "cancelled"}

RESERVATION_STATUSES = ["active", "cancelled", "fulfilled"]
# A calendar block is the same shape as a reservation (vehicle + time
# window + no-overlap rule), so it is the same table with a different kind
# rather than a parallel entity the calendar would have to merge by hand.
RESERVATION_KINDS = ["reservation", "service"]

DEFECT_STATUSES = ["new", "in_progress", "resolved"]
DEFECT_PRIORITIES = ["low", "normal", "high", "critical"]

SERVICE_TYPES = [
    "vymena_oleje", "filtry", "brzdy", "pneumatiky", "oprava", "pravidelny_servis", "stk", "jine",
]

# Papíry k vozidlu. "faktura" a "doklad" patří k servisnímu záznamu nebo
# výdaji (VehicleDocument.service_id / expense_id) - v seznamu dokumentů
# vozidla se nezobrazují, aby se účtenky nemíchaly mezi TP a zelenou kartu.
DOCUMENT_TYPES = ["tp", "otp", "zelena_karta", "pojistka", "leasing", "jine"]
RECEIPT_DOCUMENT_TYPES = ["faktura", "doklad"]
ALL_DOCUMENT_TYPES = DOCUMENT_TYPES + RECEIPT_DOCUMENT_TYPES

WHEEL_SEASONS = ["summer", "winter"]

# Druhy výdajů. "palivo" a "nabijeni" tu jsou pro nákupy mimo jízdu
# (kanystr, faktura za tankovací kartu); tankování v rámci jízdy se
# eviduje v TripFueling a do přehledu výdajů se načítá odtamtud, ne
# přepisem - viz docs/ROZHODNUTI.md R27.
EXPENSE_TYPES = [
    "palivo", "nabijeni", "servis", "pneumatiky", "stk", "dalnicni_znamka",
    "pojisteni", "myti", "ostatni",
]

# Every attachment is an image processed through app/core/photos.py. Which
# workflow produced it matters - the odometer shot at trip start is what
# makes the km value evidential (zadání 8/11) - so the kind is stored, not
# inferred from which FK happens to be set.
ATTACHMENT_KINDS = [
    "vehicle_photo", "odometer_start", "odometer_end", "fuel_receipt", "defect_photo", "service_invoice",
    "wheel_photo",
]

NOTIFICATION_KINDS = [
    "reservation", "trip_start", "trip_end", "defect", "service_due", "stk", "vignette", "insurance", "oil",
    "approval_request", "approval_decision",
]


class Vehicle(Base):
    """One vehicle. Rows are never deleted (history must stay intact) -
    `is_active` retires a vehicle from new use, `deleted_at` is reserved
    for a genuine mistaken entry."""

    __tablename__ = "vehicles"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    # Human-assigned internal label (e.g. "VOZ-01") - for staff to read and
    # search. Deliberately NOT the public QR identifier (see qr_token).
    internal_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    license_plate: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    vin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    year_of_manufacture: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vehicle_type: Mapped[str] = mapped_column(String(30), nullable=False, default="osobni")
    fuel_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Both, not one generic "capacity": a plug-in hybrid really does have a
    # tank AND a battery, and the two are never interchangeable.
    tank_capacity_l: Mapped[float | None] = mapped_column(Numeric(6, 1), nullable=True)
    battery_capacity_kwh: Mapped[float | None] = mapped_column(Numeric(6, 1), nullable=True)

    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="available")

    # Požadavek B: vozidlo, které si nelze prostě vzít. Řidič musí nejdřív
    # dostat souhlas odpovědné osoby nebo administrátora (TripRequest).
    # Vozidla bez tohoto příznaku fungují beze změny.
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Požadavek D: "all" = vidí každý, "restricted" = jen odpovědná osoba
    # a administrátor. Vynucuje se dotazem v repozitáři, ne až v šabloně -
    # viz app/core/access.py:visible_vehicles_condition.
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="all")

    # Public, unguessable identifier the printed QR code encodes
    # (/kniha-jizd/v/<qr_token>) - never the DB primary key, never the
    # license plate, so relabeling either never invalidates a printed
    # sticker, and the code itself carries no sensitive data (zadání 6).
    qr_token: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)

    # Current state (zadání 5). Maintained from closed trips, or from an
    # explicit administrative correction - never lowered by an ordinary
    # trip (enforced in trips/service.py).
    current_odometer_km: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Percent 0-100 of the tank, or of the battery on an electric vehicle -
    # the number means the same thing to a driver either way, only the
    # label differs (see app/core/fuel.py).
    current_fuel_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Povinné termíny (zadání 7/19) - rendered with the configurable traffic
    # light, and the source of the STK/vignette/insurance notifications.
    stk_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    vignette_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Pojištění. Platnost je čtvrtý hlídaný termín (stejný semafor jako
    # STK), číslo pojistky a pojišťovna jsou údaje, které řidič potřebuje
    # mít po ruce přímo u vozidla při nehodě - proto na kartě vozidla, ne
    # jen schované v naskenované zelené kartě mezi dokumenty.
    insurance_company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    insurance_policy_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    insurance_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Servisní intervaly (zadání 17). The next oil change is computed from
    # these, never stored - see app/core/fleet_status.py.
    last_oil_change_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_oil_change_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    oil_interval_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    oil_interval_months: Mapped[int | None] = mapped_column(Integer, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    responsible_user: Mapped["User | None"] = relationship()  # noqa: F821 - app.models.core.User
    photos: Mapped[list["Attachment"]] = relationship(
        viewonly=True, order_by="Attachment.created_at",
        primaryjoin=lambda: and_(
            Vehicle.id == Attachment.vehicle_id,
            Attachment.kind == "vehicle_photo",
            Attachment.deleted_at.is_(None),
        ),
    )


class VehicleAssignment(Base):
    """History of who was the responsible person for a vehicle and when
    (zadání 26 - audit/history on important data). Vehicle.responsible_user_id
    stays the fast "who is it right now" pointer; this table is the record
    of how it got there. Append-only: a handover closes the open row
    (valid_to) and opens a new one."""

    __tablename__ = "vehicle_assignments"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )

    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])  # noqa: F821


class VehicleDocument(Base):
    """TP / OTP / zelená karta / ... (zadání 18). Kept apart from
    Attachment because a document is not an image-pipeline artefact: it may
    be a PDF, is never resized, and carries validity metadata.

    Reading is open to anyone who can see the vehicle - a driver stopped by
    the police needs the green card, so gating it behind vehicle-manage
    would make the feature useless in the field. Uploading and deleting is
    vehicle-manage only (see modules/documents/service.py and
    docs/ROZHODNUTI.md R24)."""

    __tablename__ = "vehicle_documents"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doc_type: Mapped[str] = mapped_column(String(30), nullable=False, default="jine")
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    # Doklad patřící k servisnímu záznamu nebo výdaji. Obojí prázdné =
    # papír k vozidlu (TP, OTP, zelená karta), který se ukazuje v sekci
    # Dokumenty. Díky tomu má PDF účtenka stejné úložiště, autorizaci i
    # soft delete jako ostatní dokumenty - žádný třetí mechanismus na
    # soubory.
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicle_services.id", ondelete="CASCADE"), nullable=True, index=True
    )
    expense_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicle_expenses.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Random UUID filename on disk - never the user-supplied one (no path
    # traversal, no collisions, not guessable from the URL).
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    vehicle: Mapped["Vehicle"] = relationship()
    uploader: Mapped["User | None"] = relationship()  # noqa: F821


class VehicleService(Base):
    """One service/maintenance record (zadání 17)."""

    __tablename__ = "vehicle_services"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    odometer_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service_type: Mapped[str] = mapped_column(String(30), nullable=False, default="jine")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    supplier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price_czk: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    vehicle: Mapped["Vehicle"] = relationship()
    attachments: Mapped[list["Attachment"]] = relationship(
        viewonly=True, order_by="Attachment.created_at",
        primaryjoin=lambda: and_(
            VehicleService.id == Attachment.service_id, Attachment.deleted_at.is_(None)
        ),
    )


class VehicleDefect(Base):
    """A reported fault (zadání 16). Never deleted - a resolved defect
    stays in the vehicle's history."""

    __tablename__ = "vehicle_defects"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Set when the defect was reported from inside a trip (start or end) -
    # nullable because a defect can also be reported straight from the
    # vehicle card without any trip in progress.
    trip_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.trips.id", ondelete="SET NULL"), nullable=True
    )
    reported_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )

    vehicle: Mapped["Vehicle"] = relationship()
    reporter: Mapped["User | None"] = relationship(foreign_keys=[reported_by])  # noqa: F821
    photos: Mapped[list["Attachment"]] = relationship(
        viewonly=True, order_by="Attachment.created_at",
        primaryjoin=lambda: and_(VehicleDefect.id == Attachment.defect_id, Attachment.deleted_at.is_(None)),
    )

    @property
    def is_open(self) -> bool:
        return self.status != "resolved"


class VehicleReservation(Base):
    """A claim on a time window for a vehicle (zadání 9), or - with
    kind="service" - an admin-entered out-of-service block shown the same
    way on the calendar. A DB-level EXCLUDE constraint guarantees no two
    ACTIVE windows on one vehicle ever overlap; a plain unique index cannot
    express "no overlapping ranges".

    "Expired" is deliberately not a stored status (there is no scheduler
    that could flip it, and a stale past window never blocks a future one)
    - see is_expired, a read-time property only."""

    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_fleet_reservations_end_after_start"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # NULL only for kind="service" (a block belongs to the vehicle, not to
    # a person).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="reservation")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_route: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_km: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    vehicle: Mapped["Vehicle"] = relationship()
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])  # noqa: F821

    @property
    def is_current(self) -> bool:
        if self.status != "active":
            return False
        return self.start_at <= datetime.now(timezone.utc) < self.end_at

    @property
    def is_expired(self) -> bool:
        return self.status == "active" and self.end_at < datetime.now(timezone.utc)


class Trip(Base):
    """One výpůjčka/jízda - opened at "Zahájit výpůjčku", closed at
    "Ukončit výpůjčku" (zadání 8/11/27). Rows are never deleted or reused:
    closing or cancelling only changes status/timestamps on the same row.

    The partial unique index guarantees at most one ACTIVE trip per vehicle
    at the database level, so two drivers hitting "Zahájit" at the same
    moment cannot both succeed."""

    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint(
            "end_odometer_km IS NULL OR end_odometer_km >= start_odometer_km",
            name="ck_fleet_trips_end_km_not_lower",
        ),
        Index(
            "uq_fleet_trips_one_active_per_vehicle", "vehicle_id",
            unique=True, postgresql_where=text("status = 'active'"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    primary_driver_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    start_odometer_km: Mapped[int] = mapped_column(Integer, nullable=False)
    end_odometer_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_fuel_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_fuel_level: Mapped[int | None] = mapped_column(Integer, nullable=True)

    purpose_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    purpose_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    route_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set when this trip was started against the driver's own active
    # reservation (which then flips to "fulfilled").
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.reservations.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    # Zadání 10: starting over SOMEBODY ELSE's active reservation is allowed
    # but must be confirmed, and that confirmation is kept in history.
    reservation_conflict_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.reservations.id", ondelete="SET NULL"), nullable=True
    )
    reservation_override_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Zadání 14 - orientation-only road-distance check. Never a GPS track:
    # one computed number for the typed route plus the user's own
    # explanation of any difference.
    expected_distance_km: Mapped[float | None] = mapped_column(Numeric(8, 1), nullable=True)
    distance_diff_percent: Mapped[float | None] = mapped_column(Numeric(6, 1), nullable=True)
    distance_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Požadavek A: časy jízdy jdou opravit, ale opravená hodnota se nesmí
    # tvářit jako původně naměřená. Vyplněné times_edited_at je to, co
    # odlišuje "takhle to bylo" od "takhle to někdo přepsal"; kompletní
    # historie změn je v auditu (action="trip_times_edit").
    times_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    times_edited_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )

    # Žádost, na jejímž základě jízda vznikla (vozidla s approval_required).
    # Unikátní: jedna schválená žádost = nejvýše jedna jízda, což zároveň
    # brání dvěma souběžným výjezdům na totéž schválení.
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.trip_requests.id", ondelete="SET NULL"), unique=True, nullable=True
    )

    started_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    ended_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    vehicle: Mapped["Vehicle"] = relationship()
    primary_driver: Mapped["User"] = relationship(foreign_keys=[primary_driver_id])  # noqa: F821
    extra_drivers: Mapped[list["TripDriver"]] = relationship(
        back_populates="trip", order_by="TripDriver.created_at", cascade="all, delete-orphan"
    )
    fuelings: Mapped[list["TripFueling"]] = relationship(
        back_populates="trip", order_by="TripFueling.fueled_at", cascade="all, delete-orphan"
    )
    notes: Mapped[list["TripNote"]] = relationship(
        back_populates="trip", order_by="TripNote.created_at", cascade="all, delete-orphan"
    )
    photos: Mapped[list["Attachment"]] = relationship(
        viewonly=True, order_by="Attachment.created_at",
        primaryjoin=lambda: and_(Trip.id == Attachment.trip_id, Attachment.deleted_at.is_(None)),
    )
    defects: Mapped[list["VehicleDefect"]] = relationship(
        viewonly=True, primaryjoin=lambda: Trip.id == VehicleDefect.trip_id,
    )

    @property
    def distance_km(self) -> int | None:
        if self.end_odometer_km is None:
            return None
        return self.end_odometer_km - self.start_odometer_km


class TripRequest(Base):
    """Žádost o použití vozidla, které vyžaduje schválení (požadavek B).

    Stavový automat:

        pending  -> approved   (odpovědná osoba nebo administrátor)
        pending  -> rejected   (odpovědná osoba nebo administrátor)
        pending  -> cancelled  (žadatel sám, nebo administrátor)

    Čekající žádost sama od sebe nikdy nevyprší - čeká na rozhodnutí, jak
    dlouho je potřeba. `valid_until` se nastavuje AŽ při schválení a
    znamená jedinou věc: dokdy se schválení musí proměnit v jízdu, než
    přestane platit. "Vypršelo" proto není uložený stav (nebyl by kdo ho
    přepne), ale čtená vlastnost.

    Schválení NENÍ jízda. Že se schválení použilo, se pozná z
    `Trip.request_id` (unikátní), ne ze stavu na tomhle řádku - a právě
    ta unikátnost brání dvěma souběžným výjezdům na jedno schválení.

    Řádky se nemažou ani nerecyklují."""

    __tablename__ = "trip_requests"
    __table_args__ = (
        # Nejvýše jedna čekající žádost na dvojici (vozidlo, žadatel).
        # Aplikace to kontroluje taky, kvůli srozumitelné hlášce, ale
        # záruka při souběhu je tenhle index.
        Index(
            "uq_fleet_trip_requests_one_pending", "vehicle_id", "requester_id",
            unique=True, postgresql_where=text("status = 'pending'"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requester_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    # Co s vozidlem zamýšlí - aby měl schvalovatel podle čeho rozhodnout.
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    needed_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    needed_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nastaví se až při schválení - dokdy lze schválení proměnit v jízdu.
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    vehicle: Mapped["Vehicle"] = relationship()
    requester: Mapped["User"] = relationship(foreign_keys=[requester_id])  # noqa: F821
    decider: Mapped["User | None"] = relationship(foreign_keys=[decided_by])  # noqa: F821

    @property
    def is_expired(self) -> bool:
        """Jen schválená žádost může vypršet. Čekající čeká dál."""
        if self.status != "approved" or self.valid_until is None:
            return False
        return self.valid_until < datetime.now(timezone.utc)

    @property
    def is_usable(self) -> bool:
        return self.status == "approved" and not self.is_expired

    @property
    def is_open(self) -> bool:
        return self.status == "pending"


class TripDriver(Base):
    """An additional driver on a trip (zadání 12). The primary driver is
    Trip.primary_driver_id and is never duplicated here."""

    __tablename__ = "trip_drivers"
    __table_args__ = (
        Index("uq_fleet_trip_drivers_unique", "trip_id", "user_id", unique=True),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.trips.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    trip: Mapped["Trip"] = relationship(back_populates="extra_drivers")
    user: Mapped["User"] = relationship(foreign_keys=[user_id])  # noqa: F821


class TripFueling(Base):
    """Energy put into the vehicle during a trip - diesel/petrol/LPG/CNG in
    litres, or a charge in kWh for an electric or plug-in hybrid vehicle
    (zadání 15 + elektro vozy ve vozovém parku).

    One table for both, with `quantity` + `unit`, not two parallel tables
    or a second nullable `energy_kwh` column: everything around it - the
    receipt photo, the OCR confirmation, the price, the station, the
    per-vehicle reporting - is identical, and only the unit and the wording
    differ. `unit` is stored explicitly rather than derived from the
    vehicle's fuel_type at read time, because a plug-in hybrid genuinely
    does both, and because retyping a vehicle from diesel to electric must
    never silently reinterpret its historical records as kWh.

    vehicle_id is denormalized from the trip so per-vehicle consumption
    reporting never needs a join through trips."""

    __tablename__ = "trip_fuelings"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_fleet_fuelings_quantity_positive"),
        CheckConstraint("unit IN ('l', 'kWh')", name="ck_fleet_fuelings_unit"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fueled_at: Mapped[date] = mapped_column(Date, nullable=False)
    # Litres of fuel or kWh of electricity - see `unit`.
    quantity: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(5), nullable=False, default="l")
    price_total_czk: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    price_per_unit_czk: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    # Čerpací nebo nabíjecí stanice.
    station: Mapped[str | None] = mapped_column(String(255), nullable=True)
    odometer_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whether these values came out of receipt OCR and were confirmed by
    # the user (zadání 15/25) - provenance only, never a reason to skip
    # validation.
    ocr_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    trip: Mapped["Trip"] = relationship(back_populates="fuelings")
    receipts: Mapped[list["Attachment"]] = relationship(
        viewonly=True, order_by="Attachment.created_at",
        primaryjoin=lambda: and_(TripFueling.id == Attachment.fueling_id, Attachment.deleted_at.is_(None)),
    )


class TripNote(Base):
    """Append-only note thread on a trip. Trip.note is the driver's own
    note captured at closing; this is for anything added afterwards (a
    correction explanation, a responsible person's comment) without
    overwriting what the driver originally wrote."""

    __tablename__ = "trip_notes"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    trip: Mapped["Trip"] = relationship(back_populates="notes")
    author: Mapped["User | None"] = relationship()  # noqa: F821


class WheelSet(Base):
    """Konkrétní sada kol nebo pneumatik (ne jen „letní/zimní").

    Sada patří vozidlu a zůstává mu i po sundání - podle historie se pozná,
    kolik toho na ní auto najezdilo a jak je stará. Rušení je soft delete;
    vyřazená sada nesmí zmizet z historie přezutí.

    Jestli je sada zrovna nasazená, se **neukládá** - odvozuje se z
    otevřeného WheelFitment (stejný princip jako „vypůjčené" u vozidla,
    viz docs/ROZHODNUTI.md R4)."""

    __tablename__ = "wheel_sets"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    season: Mapped[str] = mapped_column(String(10), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # "205/55 R16 91H" - volný text, normy se liší a číselník by překážel.
    size: Mapped[str | None] = mapped_column(String(50), nullable=True)

    purchased_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    purchase_odometer_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # DOT kód nese týden a rok výroby ("2124" = 21. týden 2024).
    dot_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tread_depth_mm: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    vehicle: Mapped["Vehicle"] = relationship()
    photos: Mapped[list["Attachment"]] = relationship(
        viewonly=True, order_by="Attachment.created_at",
        primaryjoin=lambda: and_(WheelSet.id == Attachment.wheel_set_id, Attachment.deleted_at.is_(None)),
    )
    fitments: Mapped[list["WheelFitment"]] = relationship(
        back_populates="wheel_set", order_by="WheelFitment.fitted_at.desc()",
        cascade="all, delete-orphan",
    )

    @property
    def age_years(self) -> float | None:
        if self.purchased_at is None:
            return None
        return round((date.today() - self.purchased_at).days / 365.25, 1)


class WheelFitment(Base):
    """Jedno období, po které byla sada na vozidle (přezutí).

    Otevřený řádek (`removed_at IS NULL`) znamená „právě nasazeno".
    Částečný unikátní index zaručuje, že jedno vozidlo nemůže mít dvě
    nasazené sady najednou - a to i při dvou souběžných requestech, což
    kontrola v aplikaci sama zaručit neumí."""

    __tablename__ = "wheel_fitments"
    __table_args__ = (
        CheckConstraint(
            "removed_odometer_km IS NULL OR removed_odometer_km >= fitted_odometer_km",
            name="ck_fleet_wheel_fitments_km_not_lower",
        ),
        Index(
            "uq_fleet_wheel_fitments_one_active_per_vehicle", "vehicle_id",
            unique=True, postgresql_where=text("removed_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    wheel_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.wheel_sets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalizované ze sady - index výš musí platit na vozidlo.
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    fitted_at: Mapped[date] = mapped_column(Date, nullable=False)
    fitted_odometer_km: Mapped[int] = mapped_column(Integer, nullable=False)
    removed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    removed_odometer_km: Mapped[int | None] = mapped_column(Integer, nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    wheel_set: Mapped["WheelSet"] = relationship(back_populates="fitments")
    vehicle: Mapped["Vehicle"] = relationship()

    @property
    def is_active(self) -> bool:
        return self.removed_at is None

    def distance_km(self, current_odometer_km: int) -> int:
        """Nájezd za tohle období. U nasazené sady se počítá proti
        aktuálnímu stavu vozidla, u sundané proti stavu při sundání."""
        end = self.removed_odometer_km if self.removed_odometer_km is not None else current_odometer_km
        return max(0, end - self.fitted_odometer_km)


class VehicleExpense(Base):
    """Výdaj na vozidlo (zadání: samostatný modul, ne součást servisu).

    Tankování a nabíjení **v rámci jízdy** se sem nepřepisuje - eviduje se
    v TripFueling a přehled výdajů ho načítá odtamtud. Typy `palivo` a
    `nabijeni` jsou tu pro nákupy mimo jízdu (kanystr, měsíční faktura za
    tankovací kartu). Viz docs/ROZHODNUTI.md R27.

    Částka s DPH je povinná, rozpad na základ a DPH nepovinný - řidič u
    pumpy má v ruce účtenku, ne účetní systém."""

    __tablename__ = "vehicle_expenses"
    __table_args__ = (
        CheckConstraint("amount_czk >= 0", name="ck_fleet_expenses_amount_not_negative"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nepovinná vazba na jízdu - „tohle jsem platil při téhle cestě".
    trip_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.trips.id", ondelete="SET NULL"), nullable=True, index=True
    )

    expense_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    odometer_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expense_type: Mapped[str] = mapped_column(String(30), nullable=False, default="ostatni")

    amount_czk: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    amount_net_czk: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    vat_czk: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Zatím vždy CZK, ale sloupec tu je, aby zahraniční tankování nešlo
    # zapsat jako by to byly koruny.
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CZK")

    supplier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    vehicle: Mapped["Vehicle"] = relationship()
    trip: Mapped["Trip | None"] = relationship()
    creator: Mapped["User | None"] = relationship()  # noqa: F821
    receipts: Mapped[list["VehicleDocument"]] = relationship(
        viewonly=True, order_by="VehicleDocument.created_at",
        primaryjoin=lambda: and_(
            VehicleExpense.id == VehicleDocument.expense_id,
            VehicleDocument.deleted_at.is_(None),
        ),
    )


class Attachment(Base):
    """Every uploaded IMAGE in the app, in one table (vehicle gallery,
    odometer shots, fuel receipts, defect photos, service invoices).

    One table rather than five near-identical ones: the upload pipeline,
    the authorization check and the download route are then written once.
    vehicle_id is NOT NULL on every row - it is what every permission check
    keys on, so an attachment can never be reached without first deciding
    access to its vehicle. The optional trip/defect/service/fueling FKs say
    what it is attached to within that vehicle.

    Vehicle DOCUMENTS (TP/OTP/...) are not here - see VehicleDocument."""

    __tablename__ = "attachments"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    trip_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.trips.id", ondelete="CASCADE"), nullable=True, index=True
    )
    defect_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicle_defects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicle_services.id", ondelete="CASCADE"), nullable=True, index=True
    )
    fueling_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.trip_fuelings.id", ondelete="CASCADE"), nullable=True, index=True
    )
    wheel_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.wheel_sets.id", ondelete="CASCADE"), nullable=True, index=True
    )

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    thumbnail_path: Mapped[str] = mapped_column(String(255), nullable=False)
    full_path: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notification(Base):
    """One notification for one recipient (zadání 20). Always recorded in
    the database and shown in-app; e-mail delivery is a best-effort extra
    on top (emailed_at / email_error), never a precondition - an SMTP
    outage must not break a trip or a reservation."""

    __tablename__ = "notifications"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    link_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Deduplication key for the recurring deadline reminders (STK/vignette/
    # oil/service) - "this vehicle, this kind, this threshold" is sent once,
    # not on every run of the reminder job.
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    vehicle: Mapped["Vehicle | None"] = relationship()
