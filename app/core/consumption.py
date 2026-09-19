"""Průměrná spotřeba z tankování (zadání 15).

Čisté funkce nad seznamem tankování — žádná databáze, žádný request,
takže se dají testovat přímo a spočítají totéž na kartě vozidla i
kdekoliv jinde.

**Proč průměr přes celé období, a ne mezi dvěma tankováními.** Přesnější
metoda „plná–plná" potřebuje vědět, jestli se tankovalo do plné nádrže,
a tedy zaškrtávátko, které řidiči budou vyplňovat nespolehlivě. Průměr
přes víc tankování se ke skutečnosti přiblíží sám a nepotřebuje o
nádrži vědět nic.

**První objem se do součtu nepočítá.** To palivo bylo v nádrži ještě
před prvním odečteným stavem tachometru, takže k ujetým kilometrům mezi
prvním a posledním tankováním nepatří. Tohle je celý trik za tím
vzorcem; bez něj spotřeba vychází systematicky vyšší.

**Jednotky se nemíchají.** Litry a kWh jsou dvě různé veličiny; u
plug-in hybridu, který tankuje obojí, se počítají zvlášť a vyjdou dvě
čísla.
"""
from dataclasses import dataclass

#: Míň než tolik km mezi prvním a posledním tankováním je příliš krátký
#: úsek na to, aby z něj průměr něco znamenal - jeden neúplný dojezd ho
#: rozhodí o desítky procent.
MIN_DISTANCE_KM = 100


@dataclass(frozen=True)
class Consumption:
    """Spotřeba v jedné jednotce (l nebo kWh) na 100 km."""

    unit: str
    per_100km: float
    distance_km: int
    total_quantity: float
    #: Z kolika tankování průměr vznikl (včetně toho prvního, jehož objem
    #: se nezapočítal - uživatel má vidět, kolik záznamů za tím stojí).
    fueling_count: int
    first_odometer_km: int
    last_odometer_km: int

    @property
    def is_reliable(self) -> bool:
        """Dá se tomu číslu věřit?

        Průměr ze dvou tankování na padesáti kilometrech je formálně
        spočítaný, ale nic neříká. Obrazovka to má rozlišit, ne to
        schovat."""
        return self.fueling_count >= 3 and self.distance_km >= MIN_DISTANCE_KM


def _usable(fuelings) -> list:
    """Tankování, která mají vše potřebné, seřazená podle tachometru.

    Bez stavu tachometru se záznam do výpočtu nedostane - to je ten
    údaj, na kterém celý vzorec stojí. Řadí se podle km, ne podle data:
    dvě tankování téhož dne jdou po sobě v pořadí, v jakém se najelo."""
    return sorted(
        (f for f in fuelings if f.odometer_km is not None and f.quantity is not None),
        key=lambda f: (f.odometer_km, f.fueled_at),
    )


def by_unit(fuelings) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for fueling in fuelings:
        grouped.setdefault(fueling.unit or "l", []).append(fueling)
    return grouped


def calculate(fuelings, unit: str = "l") -> Consumption | None:
    """Průměrná spotřeba z jednoho seznamu tankování téže jednotky.

    Vrací `None`, když se spočítat nedá - málo záznamů, nebo nulový
    nájezd. `None` znamená „zatím nevíme", ne nulu: nula by na kartě
    vozidla vypadala jako změřená hodnota."""
    usable = _usable(fuelings)
    if len(usable) < 2:
        return None

    first, last = usable[0], usable[-1]
    distance = last.odometer_km - first.odometer_km
    if distance <= 0:
        return None

    # Bez prvního objemu - viz docstring modulu.
    burned = sum(float(f.quantity) for f in usable[1:])
    if burned <= 0:
        return None

    return Consumption(
        unit=unit,
        per_100km=round(burned / distance * 100, 2),
        distance_km=distance,
        total_quantity=round(burned, 2),
        fueling_count=len(usable),
        first_odometer_km=first.odometer_km,
        last_odometer_km=last.odometer_km,
    )


def for_vehicle(fuelings) -> list[Consumption]:
    """Spotřeba za vozidlo, po jednotkách.

    Obvykle jeden prvek. Dva u vozidla, které tankuje palivo i
    elektřinu - a to je správně, sčítat litry s kilowatthodinami nelze."""
    results = []
    for unit, group in by_unit(fuelings).items():
        value = calculate(group, unit)
        if value is not None:
            results.append(value)
    # Stabilní pořadí: litry první, pak kWh.
    return sorted(results, key=lambda c: (c.unit != "l", c.unit))
