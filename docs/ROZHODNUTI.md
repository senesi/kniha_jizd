# Rozhodnutí s dopadem na datový model a bezpečnost

Zadání (kapitola 37) žádá, aby se rozhodnutí dotýkající se datového
modelu nebo bezpečnosti zapsala do dokumentace. Tenhle soubor je jejich
seznam — proč to tak je, ne jen že to tak je.

---

## R1 — Jedna tabulka pro tankování i nabíjení (`quantity` + `unit`)

**Rozhodnutí.** `fleet.trip_fuelings` má `quantity` (Numeric) a `unit`
(`'l'` / `'kWh'`) místo sloupce `liters`. Cena je `price_per_unit_czk`.

**Proč.** Ve vozovém parku jsou spalovací vozy i elektromobily. Vše
kolem záznamu je u obou identické — datum, cena, stanice, fotografie
účtenky, potvrzení OCR, reporting na vozidlo — liší se jen jednotka a
slovo („tankování" / „nabíjení"). Druhá tabulka ani druhý nullable
sloupec `energy_kwh` by nepřinesly nic než dvě větve v každém dotazu.

**Proč se `unit` ukládá a neodvozuje z vozidla.** Plug-in hybrid dělá
opravdu obojí, takže z `vehicle.fuel_type` to odvodit nejde. A hlavně:
kdyby se jednotka odvozovala za běhu, přetypování vozidla z nafty na
elektro by zpětně přeznačilo historické litry na kWh. Uložená jednotka
je součást záznamu, ne jeho interpretace.

**Kde se to projeví.** `app/core/fuel.py` je jediné místo, které
rozhoduje o jednotce a názvosloví; šablony ani routy se na `fuel_type`
neptají samy. Vozidlo má `tank_capacity_l` i `battery_capacity_kwh` —
obojí, protože hybrid má tank i baterii. `current_fuel_level` je
procento nádrže *nebo* baterie; číslo znamená pro řidiče totéž, mění se
jen popisek.

---

## R2 — Pojištění jako čtvrtý hlídaný termín

**Rozhodnutí.** Vozidlo má `insurance_company`,
`insurance_policy_number` a `insurance_valid_until`. Platnost pojištění
má vlastní semafor vedle STK, dálniční známky a oleje, a vlastní
konfigurovatelné prahy (`insurance_warn_days`, `insurance_notify_days`).

**Proč na kartě vozidla, a ne jen jako naskenovaná zelená karta mezi
dokumenty.** Číslo pojistky a pojišťovnu řidič potřebuje v okamžiku
nehody, na místě a rychle. Prohledávat PDF přílohy je tehdy to poslední,
co chce dělat. Propadlé povinné ručení je navíc horší problém, než na
jaký se přijde na silnici — patří do stejného semaforu jako STK.

---

## R3 — Rezervace se nepřekrývají díky databázi, ne díky aplikaci

**Rozhodnutí.** `fleet.reservations` má `EXCLUDE USING gist (vehicle_id
WITH =, tstzrange(start_at, end_at) WITH &&) WHERE (status = 'active')`
(migrace `0001`, vyžaduje rozšíření `btree_gist`).

**Proč.** Kontrola v aplikaci (SELECT, pak INSERT) má mezi dotazem a
zápisem okno, ve kterém projdou dvě souběžné rezervace téhož termínu.
Obyčejný UNIQUE index překryv *rozsahů* vyjádřit neumí. `WHERE status =
'active'` zajišťuje, že zrušená rezervace okamžitě uvolní termín.

**Poznámka.** Rezervace a „servis / mimo provoz" je jedna tabulka
odlišená sloupcem `kind` — mají stejný tvar (vozidlo + časové okno +
zákaz překryvu) a kalendář je pak nemusí slučovat ručně.

---

## R4 — Nejvýše jedna aktivní jízda na vozidlo, opět na úrovni databáze

**Rozhodnutí.** Částečný unikátní index
`uq_fleet_trips_one_active_per_vehicle` na `(vehicle_id) WHERE status =
'active'`.

**Proč.** Dva řidiči, kteří zmáčknou „Zahájit výpůjčku" ve stejnou
chvíli, nesmí oba uspět. Stejná úvaha jako u R3.

**Souvisí.** „Vypůjčené" proto **není** stav vozidla — odvozuje se z
existence otevřené jízdy. Uložený příznak by se dřív nebo později
rozešel se skutečností.

---

## R5 — Stav tachometru hýbe jen uzavřená jízda nebo auditovaná oprava

**Rozhodnutí.** `VehicleUpdate` schválně **neobsahuje**
`current_odometer_km`. Jediná cesta, jak hodnotu změnit mimo uzavření
jízdy, je `vehicles/service.py:correct_odometer` — vyžaduje zdůvodnění a
vždy zapisuje starou i novou hodnotu do auditu. Na jízdě to navíc hlídá
i `CHECK (end_odometer_km >= start_odometer_km)`.

**Proč.** Zadání (5 a 32) žádá, aby stav km nešel běžnou jízdou snížit.
Kdyby byl tachometr obyčejným polem formuláře, byla by to jen otázka
času.

---

## R6 — Fotografie v jedné tabulce, dokumenty zvlášť

**Rozhodnutí.** `fleet.attachments` drží **všechny** obrázky (galerie
vozidla, tachometr při startu i konci, účtenky, závady, faktury ze
servisu), rozlišené sloupcem `kind`. Dokumenty vozidla (TP, OTP, zelená
karta) mají vlastní tabulku `fleet.vehicle_documents`.

**Proč jedna tabulka na obrázky.** Nahrávací pipeline, kontrola
oprávnění a stahovací routa se pak píšou jednou. `vehicle_id` je NOT
NULL na každém řádku — je to to, na čem stojí každá kontrola přístupu,
takže se k příloze nelze dostat bez rozhodnutí o jejím vozidle.

**Proč dokumenty zvlášť.** Dokument není artefakt obrázkové pipeline:
může to být PDF, nikdy se nezmenšuje (jinak přestane být čitelný drobný
tisk), nese platnost od/do a chrání ho přísnější oprávnění.

---

## R7 — Nastavení jako key/value, ne sloupce

**Rozhodnutí.** `core.app_settings` je tabulka klíč/hodnota, typované
přístupové funkce a výchozí hodnoty jsou v `app/core/app_settings.py`.

**Proč.** Přidání dalšího prahu upozornění nemá vyžadovat migraci.
Výchozí hodnota existuje v kódu pro každý klíč, takže čerstvá databáze —
nebo databáze, kde admin nastavení nikdy neotevřel — funguje správně i
bez jediného řádku v tabulce.

---

## R8 — QR token je náhodný a sám o sobě nic neodemyká

**Rozhodnutí.** `vehicles.qr_token` je `secrets.token_urlsafe(6)`,
unikátní, uložený zvlášť od `id` i od SPZ. Nálepka vede na
`/kniha-jizd/v/<token>`, což je routa **za přihlášením**.

**Proč.** QR kód na autě je fyzicky přístupný komukoliv, kdo k vozidlu
přijde. Nesmí tedy nést citlivé údaje ani fungovat jako klíč. Token je
jen adresa; kdo ji načte, uvidí přihlašovací obrazovku a po přihlášení
se vrátí přesně k tomu vozidlu. Oddělení od `id` a SPZ znamená, že
přeznačení vozidla ani změna SPZ neznehodnotí vytištěné nálepky.

---

## R9 — Flash hlášky se předávají kódem, ne textem

**Rozhodnutí.** Po POSTu se přesměrovává s `?flash=<kód>` a text se
dohledá ve slovníku `app/core/flash.py`. Neznámý kód se nezobrazí.

**Proč.** Vypisovat text přímo z query stringu by sice nebylo XSS (Jinja
escapuje), ale stačilo by to na sociální inženýrství — komukoliv by šlo
poslat odkaz, který mu uvnitř aplikace zobrazí libovolnou větu.

---

## R10 — Vlastní session cookie a vlastní secret

**Rozhodnutí.** Cookie se jmenuje `kniha_jizd_session`,
`SESSION_SECRET_KEY` je vlastní a liší se od ostatních aplikací na VPS.

**Proč.** Na `solareg.azunimb.cz` běží víc nezávislých aplikací pod
různými cestami. Sdílené jméno cookie nebo sdílený secret by z nich
udělal jeden bezpečnostní celek — přihlášení do jedné by znamenalo
přihlášení do druhé. Aplikace mají zůstat oddělené.

---

## R11 — Fotka tachometru se ukládá dřív, než vznikne jízda

**Rozhodnutí.** Když OCR přečte jinou hodnotu, než jakou řidič zadal,
aplikace se zeptá — a fotku v tu chvíli **už uloží** a do formuláře
pošle jen její id (`odometer_attachment_id`). Po potvrzení se přílohu
jen naváže na vzniklou jízdu
(`vehicles/service.py:link_attachment_to_trip`).

**Proč.** `<input type="file">` nejde předvyplnit. Bez tohohle kroku by
se fotka mezi prvním a druhým odesláním formuláře ztratila a řidič by
musel tachometr fotit znovu — v terénu nepřijatelné.

**Co to stojí.** Když uživatel formulář v tu chvíli opustí, zůstane u
vozidla příloha bez jízdy. Do galerie vozidla se nedostane (ta bere jen
`kind="vehicle_photo"`), takže nic nerozbije.

**Bezpečnost.** `link_attachment_to_trip` ověřuje, že příloha patří
tomu vozidlu, ke kterému se jízda zakládá — jinak by podvržené id ve
skrytém poli umožnilo přivlastnit si cizí fotografii (IDOR). Pokryto
testem `test_cross_vehicle_attachment_cannot_be_hijacked`.

---

## R12 — Stav vozidla se posouvá už při zahájení jízdy

**Rozhodnutí.** `start_trip` aktualizuje `current_odometer_km` a
`current_fuel_level`, nejen `end_trip`. Posun je vždy jen nahoru
(`max(...)`).

**Proč.** Co řidič právě přečetl na tachometru, je novější údaj než
poslední uzavřená jízda. Kdyby se stav aktualizoval až při vrácení,
další člověk, který k vozidlu přijde během probíhající výpůjčky, by
viděl zastaralé číslo.

---

## R13 — Zrušená jízda nevrací tachometr zpět

**Rozhodnutí.** `cancel_trip` přepne stav na `cancelled`, doplní
poznámku s důvodem a řádek ponechá. Stav vozidla se nevrací.

**Proč.** Zrušení řeší „omylem jsem zmáčkl Zahájit", ne „ta hodnota
byla špatně". Zadaný stav km je skutečný údaj, který někdo na vozidle
viděl. Na opravu chybné hodnoty je administrativní oprava (R5), která
si vyžádá zdůvodnění.

---

## R14 — Uzavřít výpůjčku smí i správce vozidla

**Rozhodnutí.** `can_end_trip` pustí primárního řidiče, každého dalšího
řidiče, držitele `fleet.trip.manage` a správce daného vozidla
(`fleet.vehicle.manage` nebo `.own` proti `responsible_user_id`).

**Proč.** Zapomenutá otevřená výpůjčka blokuje vozidlo pro všechny
ostatní — kvůli databázovému pravidlu „nejvýše jedna aktivní jízda na
vozidlo" (R4). Musí existovat někdo, kdo ji umí zavřít, aniž by se
čekalo na administrátora. Kdo jízdu skutečně uzavřel, zůstává v
`ended_by`.

---

## R15 — Porušení EXCLUDE constraintu se odchytává savepointem, ne rollbackem

**Rozhodnutí.** `reservations/service.py` obaluje `flush()` do
`async with db.begin_nested()`. Po `IntegrityError` se **nevolá**
`await db.rollback()`.

**Proč.** Rollback celé transakce expiruje každý objekt v session.
Formulář se pak vykresluje znovu a první přístup k `user.full_name`
nebo `vehicle.license_plate` by se pokusil data dotáhnout — jenže
expirovaný atribut se načítá synchronně, takže na async session spadne
na `MissingGreenlet`. Savepoint zahodí jen neúspěšný INSERT a session
nechá plně použitelnou.

**Pozor u úprav.** Savepoint vrátí databázi, ale objekt v paměti si nové
hodnoty drží dál. Bez `db.expire(reservation)` by je autoflush při
vykreslení formuláře zapsal znovu a spadl na tomtéž constraintu — proto
tam ten `expire` je.

---

## R16 — Kalendář je postavený od volna, ne od obsazenosti

**Rozhodnutí.** `reservations/calendar.py` staví mřížku vozidlo × den,
kde výchozí stav buňky je „volno" a teprve rezervace, servisní blok
nebo probíhající jízda ji přebarví. Řádek dostane **každé** aktivní
vozidlo, i to bez jediné rezervace.

**Proč.** Zadání 9 chce, aby byl volný termín zřejmý na první pohled.
Kdyby se vykreslovaly jen existující rezervace, prázdno by znamenalo
„nevíme" místo „je volno" a vozidlo bez rezervací by na kalendáři
chybělo úplně.

**Přednost při souběhu.** Když na jeden den padne víc věcí, vyhrává
probíhající jízda před servisem a ten před rezervací: jízda je fakt,
rezervace jen nárok. V popisku buňky zůstává obojí.

**Časové pásmo.** Data jsou v databázi v UTC, ale den v kalendáři začíná
v `Europe/Prague` — jinak by rezervace od 23:00 padla na špatný den.
Proto je mezi závislostmi `tzdata` (Windows ani slim image vlastní
databázi pásem nemají).

---

## R17 — Cizí rezervaci lze přejet, vlastní se naplní

**Rozhodnutí.** Při zahájení výpůjčky se hledá aktivní rezervace
pokrývající „teď":

- je **moje** → jízda se na ni naváže (`Trip.reservation_id`) a
  rezervace přejde do stavu `fulfilled`, bez jediného dotazu navíc;
- je **cizí** (nebo servisní blok) → `TripWarning` se jménem, začátkem
  a koncem. Po potvrzení se do jízdy uloží `reservation_conflict_id` a
  `reservation_override_at`.

**Proč.** Zadání 10 výslovně zakazuje výpůjčku automaticky blokovat,
ale žádá viditelné upozornění a uložení potvrzení do historie.

**Co se nestane.** Přejetá cizí rezervace **zůstává aktivní**. Ten,
kdo si vozidlo zamluvil, o svůj záznam nepřijde jen proto, že mu ho
někdo vzal — jinak by z kalendáře zmizela stopa po tom, co se stalo.
