"""Výdaje na vozidlo.

Samostatný modul, ne součást servisní historie: mytí, dálniční známka
ani pojistka nejsou servisní úkony a cpát je tam by znepřehlednilo obojí.

**Tankování se sem nepřepisuje.** Palivo a nabíjení v rámci jízdy žijí
v `TripFueling` a přehled výdajů je odtamtud načítá jako druhý zdroj
(viz `repository.combined_totals`). Typy `palivo` a `nabijeni` tu jsou
pro nákupy mimo jízdu - kanystr, měsíční faktura za tankovací kartu.
Viz docs/ROZHODNUTI.md R27.

Povinná je částka s DPH; rozpad na základ a DPH je nepovinný. Řidič má
u pumpy v ruce účtenku, ne účetní systém.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.models.core import User
from app.models.fleet import EXPENSE_TYPES, Trip, Vehicle, VehicleExpense
from app.modules.documents import service as documents_service
from app.modules.expenses import repository

MODULE = "expenses"

# Nad tolik je to skoro jistě překlep v řádu (např. 45000 místo 4500).
LARGE_AMOUNT_CZK = 200_000


class ExpenseError(Exception):
    """Jednoznačně neplatný vstup."""


class ExpenseWarning(Exception):
    """Podezřelé, ale možná správné - projde po potvrzení."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


async def add_expense(
    db: AsyncSession, *, vehicle: Vehicle, actor: User, expense_date: date | None,
    expense_type: str, amount_czk: float | None, amount_net_czk: float | None,
    vat_czk: float | None, currency: str, supplier: str | None, odometer_km: int | None,
    note: str | None, trip: Trip | None, confirmations: set[str],
    receipt: tuple[str, str | None, bytes] | None = None,
) -> VehicleExpense:
    if expense_date is None:
        raise ExpenseError("Datum je povinné.")
    if expense_date > date.today():
        raise ExpenseError("Datum nemůže být v budoucnosti.")
    if expense_type not in EXPENSE_TYPES:
        raise ExpenseError("Vyberte druh výdaje.")
    if amount_czk is None:
        raise ExpenseError("Částka je povinná.")
    if amount_czk < 0:
        raise ExpenseError("Částka nemůže být záporná.")
    for label, value in (("Základ daně", amount_net_czk), ("DPH", vat_czk)):
        if value is not None and value < 0:
            raise ExpenseError(f"{label} nemůže být záporný.")
    if odometer_km is not None and odometer_km < 0:
        raise ExpenseError("Stav tachometru nemůže být záporný.")
    if trip is not None and trip.vehicle_id != vehicle.id:
        raise ExpenseError("Vybraná jízda patří jinému vozidlu.")

    currency = (currency or "CZK").strip().upper()[:3] or "CZK"

    if amount_czk > LARGE_AMOUNT_CZK and "large_amount" not in confirmations:
        raise ExpenseWarning(
            "large_amount",
            f"Částka {amount_czk:,.2f} {currency} je nezvykle vysoká. Je to správně?"
            .replace(",", " ").replace(".", ","),
        )
    _check_vat_split(amount_czk, amount_net_czk, vat_czk, confirmations)

    expense = VehicleExpense(
        vehicle_id=vehicle.id,
        trip_id=trip.id if trip else None,
        expense_date=expense_date,
        odometer_km=odometer_km,
        expense_type=expense_type,
        amount_czk=amount_czk,
        amount_net_czk=amount_net_czk,
        vat_czk=vat_czk,
        currency=currency,
        supplier=(supplier or "").strip() or None,
        note=(note or "").strip() or None,
        created_by=actor.id,
    )
    db.add(expense)
    await db.flush()

    if receipt is not None:
        await _store_receipt(db, expense=expense, vehicle=vehicle, actor=actor, receipt=receipt)

    await log_action(
        db, user_id=actor.id, action="create", module=MODULE, entity_type="expense",
        entity_id=str(expense.id),
        after_data={
            "vehicle_id": str(vehicle.id), "trip_id": str(trip.id) if trip else None,
            "expense_type": expense_type, "amount_czk": amount_czk, "currency": currency,
            "expense_date": expense_date.isoformat(),
            "confirmations": sorted(confirmations),
        },
    )
    await db.commit()
    return await repository.get(db, expense.id)


async def add_receipt(
    db: AsyncSession, *, expense: VehicleExpense, actor: User, receipt: tuple[str, str | None, bytes],
) -> None:
    """Doplnění nebo výměna dokladu. Starý zůstává (soft delete v
    dokumentech) - doklad je podklad, ne pracovní soubor."""
    await _store_receipt(db, expense=expense, vehicle=expense.vehicle, actor=actor, receipt=receipt)
    await log_action(
        db, user_id=actor.id, action="receipt_add", module=MODULE, entity_type="expense",
        entity_id=str(expense.id), after_data={"filename": receipt[0]},
    )
    await db.commit()


async def _store_receipt(
    db: AsyncSession, *, expense: VehicleExpense, vehicle: Vehicle, actor: User,
    receipt: tuple[str, str | None, bytes],
) -> None:
    """Doklad se ukládá jako VehicleDocument navázaný na výdaj.

    Znovu použité úložiště, které už umí PDF i fotografie, ověřuje
    magické bajty, dává souborům náhodná jména a má autorizované
    stahování - není důvod psát třetí mechanismus na soubory."""
    filename, _content_type, data = receipt
    await documents_service.add_document(
        db, vehicle=vehicle, actor=actor, doc_type="doklad",
        title=f"Doklad – {filename}", valid_from=None, valid_to=None, note=None,
        filename=filename, content_type=_content_type, data=data,
        expense_id=expense.id, commit=False,
    )


async def delete_expense(db: AsyncSession, *, expense: VehicleExpense, actor: User) -> None:
    """Soft delete - výdaj je účetní podklad."""
    expense.deleted_at = datetime.now(timezone.utc)
    await log_action(
        db, user_id=actor.id, action="delete", module=MODULE, entity_type="expense",
        entity_id=str(expense.id),
        before_data={"expense_type": expense.expense_type, "amount_czk": expense.amount_czk},
    )
    await db.commit()


def _check_vat_split(amount, net, vat, confirmations: set[str]) -> None:
    """Když uživatel vyplní rozpad DPH, měl by sedět. Nesedící součet je
    varování - na účtence se občas zaokrouhluje jinak."""
    if net is None or vat is None or "vat_mismatch" in confirmations:
        return
    expected = float(net) + float(vat)
    if abs(expected - float(amount)) > 1.0:
        raise ExpenseWarning(
            "vat_mismatch",
            f"Základ {net:g} + DPH {vat:g} = {expected:g}, ale celková částka je {amount:g}. "
            "Zkontrolujte hodnoty.",
        )
