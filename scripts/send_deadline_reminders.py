"""Rozešle připomínky blížících se termínů vozidel (zadání 19/20).

Použití (v běžícím kontejneru aplikace):
    python -m scripts.send_deadline_reminders          # ostrý běh
    python -m scripts.send_deadline_reminders --dry-run  # jen vypíše

Určeno ke spuštění jednou denně z cronu. Skript sám nic neplánuje -
opakované spouštění je práce systému, ne aplikace, a takhle jde běh
kdykoliv ručně zopakovat nebo si ho nanečisto prohlédnout.

**Opakované spuštění nevadí.** Každá připomínka nese `dedupe_key`
složený z vozidla, termínu, jeho data a stupně naléhavosti, takže druhý
běh téhož dne nikomu nepošle nic navíc. Jakmile se termín posune nebo
zežloutne na červenou, klíč se změní a připomínka projde znovu.

Komu dorazí, rozhoduje individuální nastavení každého příjemce - viz
app/modules/notifications/preferences.py.
"""
import argparse
import asyncio

from sqlalchemy import select

from app.core import app_settings
from app.core.db import async_session_factory
from app.core.fleet_status import vehicle_deadlines
from app.models.fleet import Vehicle
from app.modules.notifications import service as notifications


async def run(*, dry_run: bool) -> int:
    sent_total = 0
    async with async_session_factory() as db:
        thresholds = await app_settings.get_all(db)
        vehicles = (await db.execute(
            select(Vehicle).where(Vehicle.is_active.is_(True)).order_by(Vehicle.license_plate)
        )).scalars().all()

        for vehicle in vehicles:
            for deadline in vehicle_deadlines(vehicle, thresholds):
                if not deadline.is_actionable:
                    continue

                if dry_run:
                    recipients = await notifications.deadline_recipients(db, vehicle)
                    print(f"  {vehicle.license_plate:12} {deadline.label:24} {deadline.level:7}"
                          f" -> {len(recipients)} adresátů (bez ohledu na jejich nastavení)")
                    continue

                sent = await notifications.notify_vehicle_deadline(db, vehicle=vehicle, deadline=deadline)
                if sent:
                    print(f"  {vehicle.license_plate:12} {deadline.label:24} -> odesláno {len(sent)}×")
                sent_total += len(sent)

    return sent_total


def main() -> None:
    parser = argparse.ArgumentParser(description="Připomínky termínů vozidel")
    parser.add_argument("--dry-run", action="store_true",
                        help="jen vypíše, co by se odeslalo; nic nezapíše ani neodešle")
    args = parser.parse_args()

    print("Připomínky termínů" + (" (nanečisto)" if args.dry_run else ""))
    total = asyncio.run(run(dry_run=args.dry_run))
    if not args.dry_run:
        print(f"Hotovo, odesláno celkem: {total}")


if __name__ == "__main__":
    main()
