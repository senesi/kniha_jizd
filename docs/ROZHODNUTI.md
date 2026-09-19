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

---

## R18 — Skryté vozidlo vrací 404, ne 403

**Rozhodnutí.** `assert_vehicle_visible` odpovídá `404 Vozidlo nebylo
nalezeno`. Výpisy filtruje podmínka přidaná přímo do dotazu
(`visible_vehicles_condition`), ne šablona.

**Proč 404.** U vozidla s `visibility="restricted"` je i samotná věta
„tohle vozidlo existuje, jen na něj nemáš právo" informace, která se ven
dostat nemá. Přes QR token by navíc rozdíl mezi 403 a 404 umožnil
ověřovat, která nálepka patří kterému autu — stačilo by projít parkoviště
a načíst kódy.

**Proč filtrovat v dotazu.** Skrýt řádek až v šabloně znamená, že se data
stejně načtou a dřív nebo později někde proklouznou — v exportu, v
JSON odpovědi, v počtu na dashboardu. Filtr v `WHERE` je jediné místo,
kde to platí pro všechny výstupy naráz.

**Pozor.** `SEE_ALL_CODES` obsahuje výhradně `fleet.vehicle.manage`.
Při prvním pokusu tam bylo i `fleet.logbook.view` — jenže to má i
odpovědná osoba, takže by viděla cizí skrytá vozidla. Odhalil to test
`test_responsible_person_sees_own_restricted_vehicle`.

---

## R19 — Schválení není jízda

**Rozhodnutí.** `fleet.trip_requests` má stavy
pending/approved/rejected/cancelled. Že se schválení **použilo**, se
pozná z `Trip.request_id` (unikátní sloupec), ne ze stavu na žádosti.

**Proč.** Kdyby „použito" byl pátý stav, musel by ho někdo přepínat a
dva souběžné výjezdy na totéž schválení by mohly projít oba. Unikátní
`Trip.request_id` je stejná záruka jako u rezervací (R4) — druhý zápis
prostě neprojde.

**Vypršení.** `valid_until` se nastavuje až při schválení a čekající
žádost nikdy nevyprší sama od sebe — čeká na rozhodnutí, jak dlouho je
potřeba. „Vypršelo" je proto čtená vlastnost, ne uložený stav: neexistuje
nic, co by ho spolehlivě přepnulo.

**Kdo nežádá.** Odpovědná osoba a administrátor si vozidlo berou přímo
(`needs_approval` je pro ně vždy False) — žádost sami sobě by byla jen
obřad navíc.

---

## R20 — Opravený čas jízdy se nesmí tvářit jako naměřený

**Rozhodnutí.** `Trip.times_edited_at` / `times_edited_by`. Vyplněný
`times_edited_at` je jediné, co odlišuje „takhle to bylo" od „takhle to
někdo přepsal"; v UI se vedle času zobrazí odznak *upraveno*. Původní i
nová hodnota jde do auditu (`action="trip_times_edit"`) a důvod navíc
jako poznámka k jízdě.

**Co se odmítá tvrdě.** Konec před začátkem, čas v budoucnosti, chybějící
zdůvodnění, uzavřená jízda bez času konce.

**Co jen varuje.** Překryv s jinou jízdou téhož vozidla — typicky známka
překlepu, ale legitimní třeba u opravy dvou po sobě jdoucích jízd. Projde
po potvrzení, které skončí v auditu.

---

## R21 — Náhled vozidla vyžaduje eager loading

**Rozhodnutí.** Náhledová fotka je první `Attachment` s
`kind="vehicle_photo"`; žádný nový sloupec. Každý repozitář, jehož data
se vykreslují makrem `thumb()`, musí vozidlo načítat
`selectinload(...).selectinload(Vehicle.photos)`.

**Proč to hlídat.** Bez toho se galerie dotahuje až v šabloně, tedy mimo
async kontext, a stránka spadne na `MissingGreenlet` — ne na chybějící
obrázek. Objevilo se to u seznamu žádostí, u závad a znovu u účtenek
v detailu jízdy (`Trip.fuelings` → `TripFueling.receipts`).

**Obecné pravidlo:** cokoliv, co šablona projde cyklem, musí být
načtené dopředu — včetně vnořené úrovně. `selectinload(A.b)` nestačí,
když se vykresluje `a.b[i].c`; musí to být
`selectinload(A.b).selectinload(B.c)`. Na async session to není
optimalizace, ale podmínka funkčnosti. Je to nejčastější chyba v tomhle
projektu — při přidání sekce do šablony je to první věc ke kontrole.

**Bez fotky** se kreslí neutrální silueta auta, ne prázdné místo: řidič
musí poznat, že fotka chybí, a ne že se nenačetla.

---

## R22 — Tankování a nabíjení mají jeden formulář

**Rozhodnutí.** `app/modules/fuelings/` obsluhuje obojí. O jednotce a
názvosloví rozhoduje výhradně `app/core/fuel.py` (viz R1); modul sám se
na `fuel_type` neptá. U elektromobilu se litry vůbec nenabídnou a
podvržená jednotka ve formuláři je odmítnuta na serveru, ne jen skrytá
v UI.

**Povinné je jen datum a množství.** Cena, cena za jednotku, stanice,
stav km, druh paliva, účtenka i poznámka jsou nepovinné a schované pod
rozbalovátkem — zadání 15 to tak chce a řidič u pumpy nemá čas na
dvacet polí.

**Cena za jednotku se dopočítá, ale nikdy nepřepíše.** Když ji uživatel
zadá, platí jeho hodnota: na účtence bývá zaokrouhleno jinak, než by
vyšlo z dělení.

**Dva stropy, ne jeden.** `fueling_max_liters` (300) a
`charging_max_kwh` (250) jsou samostatná nastavení — 280 kWh je zjevná
chyba, 280 litrů u nákladního auta ne.

**Co blokuje a co jen varuje** (zadání 32):
- tvrdě: chybějící datum či množství, množství ≤ 0, datum v
  budoucnosti, množství nad stropem, jednotka, kterou vozidlo nemá
- varováním: množství nad kapacitu nádrže/baterie (kanystr je
  legitimní), stav km nižší než na začátku jízdy

---

## R23 — OCR účtenky: parser je oddělený od enginu

**Rozhodnutí.** `ocr.parse_receipt_text()` je čistá funkce nad
rozpoznaným textem, `ocr.read_receipt()` jen obaluje engine. Žádné pole
v `ReceiptReading` není povinné.

**Proč to dělit.** Chyby nevznikají v engine, ale při čtení českých
účtenek: desetinná čárka, mezera v tisících (`1 887,15`), jednotka
přilepená k číslu, a hlavně tiskárny na pumpách, které neumí
diakritiku (`Kc/l` místo `Kč/l`). Tohle všechno se dá testovat bez
jakéhokoliv OCR enginu — a testuje se.

**Nikdy se neuloží samo.** Přečtené hodnoty se zobrazí jako návrh s
tlačítkem „Vyplnit těmito hodnotami"; uživatel je může přepsat a pak
formulář teprve odešle. Uložení nese `ocr_confirmed=True` jako
provenienci — ne jako důvod přeskočit validaci.

**Účtenka se ukládá dřív než tankování**, stejně jako fotka tachometru
u jízdy (R11) a ze stejného důvodu: `<input type="file">` nejde
předvyplnit, takže by se mezi krokem „ukaž návrh" a „potvrď" ztratila.

---

## R24 — Dokumenty vozidla: čtení všem, kdo vidí vozidlo

**Rozhodnutí.** Dokumenty (TP, OTP, zelená karta, pojistka) si smí
otevřít **každý, kdo vidí vozidlo**. Nahrávat a mazat je smí jen správce
vozidla.

**Proč změna.** Původní komentář v modelu říkal, že dokumenty jsou „za
přísnějším oprávněním než běžné čtení". Při implementaci se ukázalo, že
to nejde dohromady s tím, na co ty dokumenty jsou: zelená karta a
technický průkaz jsou přesně to, co řidič potřebuje v ruce při dopravní
kontrole nebo po nehodě. Kdyby se k nim nedostal, funkce by v terénu
byla k ničemu.

Zadání 4 běžnému uživateli zakazuje dokumenty **měnit**, ne je vidět —
takže to původní čtení nebylo požadavkem, jen mým vlastním
přitvrzením. Komentář v modelu jsem srovnal s tímhle rozhodnutím, aby
si dvě místa neodporovala.

**Co zůstává.** Soubory leží pod náhodnými UUID jmény v adresáři, který
není servírovaný staticky; každé stažení projde autorizovanou routou a
kontrolou viditelnosti vozidla. Neexistuje uhodnutelná URL (zadání
18/30). Odpověď má `Cache-Control: private`, aby dokument neskončil ve
sdílené cache. Soft-smazaný dokument je nedostupný okamžitě — repozitář
ho nenajde.

---

## R25 — Výměna oleje uzavírá smyčku se semaforem

**Rozhodnutí.** Servisní záznam typu `vymena_oleje` nabídne (zaškrtnuto
předem) přepis `last_oil_change_at` a `last_oil_change_km` na vozidle.
U jiných typů úkonu se příznak ignoruje, i když přijde ve formuláři.

**Proč.** Bez toho by šlo zapsat výměnu oleje do servisní knihy a
semafor na kartě vozidla by dál svítil oranžově — dva zdroje pravdy o
téže věci. Zadání 17 chce, aby se další termín dopočítal z intervalů;
tohle je to, co ty intervaly posouvá.

**Proč volitelné.** Zpětně dopsaný starý úkon nesmí přepsat novější
stav. Proto zaškrtávátko, ne automatika.

---

## R26 — Servisní historie se maže jen naoko

**Rozhodnutí.** `VehicleService.deleted_at` (soft delete), stejně jako u
příloh a dokumentů. Smazaný záznam zmizí z výpisů, ale řádek zůstává.

**Proč.** Servisní historie je podklad pro posouzení technického stavu i
hodnoty vozidla. Omylem smazaný záznam o výměně rozvodů je informace,
která se nedá rekonstruovat.

**Co jde natvrdo.** `TripFueling` se maže úplně — je to jeden údaj o
množství a ceně, typicky smazaný do minuty po překlepu, a jeho účtenka
(příloha) zůstává i tak.

---

## R27 — Tankování se do výdajů nepřepisuje, jen načítá

**Rozhodnutí.** `TripFueling` (tankování u jízdy) zůstává jediným místem,
kde se eviduje palivo natankované během jízdy. Modul výdajů ho
**nekopíruje** — přehled nákladů ho načítá jako druhý zdroj
(`expenses/repository.combined_totals`) a zobrazuje odděleně jako
„z jízd".

**Proč.** Kdyby tankování zakládalo i výdaj, musel by někdo řešit, co se
stane při opravě nebo smazání jednoho z nich. Dvojí zápis téhož faktu je
přesně ten druh duplicity, který se časem rozejde.

**Proč typy `palivo` a `nabijeni` přesto existují.** Pro nákupy **mimo
jízdu**: kanystr do zásoby, měsíční faktura za tankovací kartu. Formulář
u těchto dvou typů zobrazí upozornění, že tankování během jízdy patří
k jízdě.

**Důsledek.** „Celkové náklady" = zapsané výdaje + tankování z jízd.
Kdyby se sčítaly jen výdaje, přehled by u většiny vozidel lhal o
největší položce.

---

## R28 — Nasazená sada kol se odvozuje, neukládá

**Rozhodnutí.** `WheelFitment` s `removed_at IS NULL` znamená „právě
nasazeno". Na `WheelSet` ani na `Vehicle` žádný příznak není. Částečný
unikátní index `uq_fleet_wheel_fitments_one_active_per_vehicle` zaručuje
nejvýš jednu nasazenou sadu na vozidlo — i při dvou souběžných
requestech.

**Proč.** Stejná úvaha jako u „vypůjčeného" vozidla (R4) a rezervací
(R3): uložený příznak se dřív nebo později rozejde se skutečností,
protože ho musí někdo přepínat.

**Nájezd na sadě** se počítá stejným způsobem: nasazená sada proti
aktuálnímu stavu vozidla, sundaná proti stavu při sundání. Sečteno přes
všechna období, kdy byla na voze — sada se může vracet.

**Přezutí je jedna transakce.** Sundat starou a nasadit novou proběhne
buď obojí, nebo nic; jinak by vozidlo zůstalo bez kol.

**Co varuje, co zakazuje.** Dezén pod zákonným minimem (letní 1,6 mm,
zimní 4 mm) je **varování** — aplikace nemá suplovat technickou
kontrolu, ale nemá to ani mlčky přejít. Stav km nižší než při nasazení
předchozí sady je **chyba**, protože by rozbil výpočet nájezdu.

---

## R29 — Doklad je dokument, ne třetí mechanismus na soubory

**Rozhodnutí.** Doklady k servisnímu záznamu a k výdaji se ukládají jako
`VehicleDocument` s vyplněným `service_id` nebo `expense_id`. Seznam
dokumentů vozidla takové řádky filtruje pryč.

**Proč.** V projektu už byla dvě úložiště: `Attachment` (obrázky,
zmenšování, náhledy) a `VehicleDocument` (soubor tak jak je, umí PDF,
autorizované stahování, soft delete). Faktura přijatá e-mailem je PDF,
takže obrázkovou cestou neprojde — a psát třetí mechanismus jen kvůli
tomu by znamenalo potřetí řešit tytéž věci: ověření obsahu, náhodná
jména, oprávnění, mazání.

**Co to vyřešilo navíc.** Servisní záznam dosud uměl přiložit jen
fotografii. Teď zvládne i PDF, aniž by k tomu přibyl jediný nový
model.

**Proč filtrovat.** Bez toho by se mezi technický průkaz a zelenou kartu
míchaly účtenky z myčky. Doklad se zobrazuje u svého záznamu.

---

## R30 — Probíhající jízda obsazuje v kalendáři jen dnešek

**Rozhodnutí.** Otevřená jízda (`ended_at IS NULL`) obarví v kalendáři
dny od svého začátku po **konec dneška**, ne po konec zobrazeného
rozsahu. Následující dny zůstávají volné a rezervovatelné.

**Proč.** Otevřená jízda nemá konec, a když se brala doslova, „jede" se
táhlo přes celý zbytek týdne — vozidlo pak nešlo rezervovat na žádný z
těch dnů. Přitom to, že se řidič dneska nevrátil, neříká nic o pátku.
Kalendář má ukazovat, co víme; o budoucnosti u otevřené jízdy nevíme nic.

**Proč zrovna dnešek.** Je to poslední den, o kterém máme informaci.
Jakmile jízda skutečně přeteče do dalšího dne, ten den se obarví sám
při příštím zobrazení — nic se nemusí přepočítávat ani ukládat, stejně
jako u zbytku odvozených stavů (R4, R28).

**Co se tím nemění.** Rezervaci na dobu probíhající jízdy aplikace
nikdy nezakazovala — překryv hlídá EXCLUDE constraint mezi rezervacemi
(R3), ne jízdy. Šlo čistě o to, že zabarvená buňka nebyla klikací.

**Den startu je vidět vždycky.** Kdyby měla otevřená jízda ručně
opravený čas startu do budoucna (požadavek A), použije se konec dne, kdy
začala. Záznam tak z kalendáře nezmizí.

---

## R31 — Miniatura dokumentu se vyrábí až při zobrazení

**Rozhodnutí.** Náhled prvního listu dokumentu (`/documents/{id}/preview`)
se nerenderuje při nahrání, ale při prvním zobrazení, a pak zůstává na
disku vedle originálu jako `<stejné-uuid>_preview.jpg`.

**Proč ne při nahrání.** Dokumenty na produkci už jsou. Náhled při
nahrání by znamenal migraci, dávkový přepočet a sloupec navíc — a
dokumenty nahrané dřív by ho stejně nedostaly, dokud by je někdo znovu
nenahrál.

**Proč vedle originálu.** Miniatura technického průkazu prozradí
prakticky totéž co on sám, takže patří do téhož adresáře mimo dosah
webserveru, pod stejně náhodné jméno, a její routa má **totožnou
autorizaci** jako stahování: u skrytého vozidla končí stejným 404
(zadání 18/30).

**Náhled je pohodlí, ne funkce.** Poškozený soubor, PDF zašifrované
heslem nebo HEIC, který Pillow bez `pillow-heif` neotevře — ve všech
případech se vrátí `None` a vykreslí se neutrální ikona. Seznam
dokumentů musí fungovat dál, stejně jako u OCR a map (zadání 14/34).
Proto je i `pypdfium2` importované v `try/except`: chybějící renderer
PDF nesmí shodit start aplikace.

**Zápis přes `os.replace`.** Dva souběžné požadavky na týž dokument by
jinak psaly do jednoho souboru naráz a druhý by si přečetl půlku JPEGu.

---

## R32 — Servisní úkon má typů víc, ne jeden

**Rozhodnutí.** `VehicleService.service_type` (jedna hodnota) se mění na
`service_types` (pole). Jedna návštěva servisu bývá víc úkonů najednou —
vymění se olej, filtry a k tomu se přehodí brzdové destičky.

**Proč pole, a ne „hlavní typ + vedlejší".** Který z nich je hlavní?
Odpověď na to nikdo nezná a stejně by se lišila případ od případu.
Vedlejší typy by navíc znamenaly dva sloupce, které musí zůstat
v souladu — přesně to, čemu se projekt vyhýbá jinde (R1, R4, R28).

**Proč se starý sloupec zahodil.** Držet obojí by znamenalo druhý zdroj
pravdy o téže věci. Migrace `0004` data převádí, nemaže: každý existující
záznam dostane jednoprvkové pole s hodnotou, kterou měl. Downgrade vrací
první prvek — zpátky se víc typů nevejde a migrace to říká nahlas.

**Prázdné pole hlídá databáze**, ne jen aplikace: CHECK
`cardinality(service_types) > 0`. Servisní úkon bez typu není záznam,
o kterém by šlo cokoliv zjistit.

**Pořadí se normalizuje** podle číselníku `SERVICE_TYPES`, ne podle toho,
jak uživatel klikal — jinak by dva stejné úkony vypadaly v seznamu
pokaždé jinak.

**Semafor prohlídky** se posune, když je `vymena_oleje` mezi vybranými.
Dřív musel být jediný, takže kdo zapsal olej spolu s brzdami, přišel o
přepočet dalšího termínu.

---

## R33 — Na přílohu k servisu stačí jedno tlačítko

**Rozhodnutí.** `/services/{id}/attachments` přijímá fotografii i PDF.
Samostatná routa `/services/{id}/documents` zanikla a formulář pro
založení úkonu bere `accept=".pdf,image/*"`.

**Proč.** Faktura ze servisu přijde jednou vyfocená mobilem a podruhé
e-mailem jako PDF. Dvě tlačítka vedle sebe („+ foto" / „+ doklad")
nutila uživatele rozhodnout něco, co pozná server sám z přípony — a kdo
by se spletl, dostal by chybu místo uložené faktury.

**Kde se rozhoduje.** V service vrstvě (`_store_invoice`), na jednom
místě. Obrázek, který zvládne obrázková pipeline, jde jako `Attachment`
(zmenšování, náhledy); všechno ostatní — PDF, ale i HEIC, které Pillow
neotevře — jde jako `VehicleDocument` se zaplněným `service_id` (R29).
Router ani šablona o té volbě nevědí.

**Co to vyřešilo navíc.** Formulář pro **založení** úkonu dosud bral jen
`image/*`, takže PDF šlo přiložit až dodatečně ze seznamu. Teď jde rovnou.

---

## R34 — Filtr knihy jízd je jeden objekt, ne parametry rozstrkané po routách

**Rozhodnutí.** `LogbookFilter` je zmrazená datová třída, kterou parsuje
`from_params()` a spotřebovává jak výpis, tak export. Dotazy se skládají
na jednom místě (`logbook/repository.py:_base_query`).

**Proč.** Zadání 23 chce, aby export respektoval aktivní filtry. Kdyby
si obrazovka skládala podmínky sama a export znovu, po prvním přidaném
filtru se rozejdou — a uživatel dostane do XLSX jiná data, než jaká měl
před sebou. Tohle není hypotetické riziko, je to nejčastější chyba
tabulkových exportů.

**Parsování je shovívavé.** Nesmyslné datum, neexistující UUID nebo
vymyšlený kód účelu se zahodí a filtr se prostě nepoužije, místo 422.
Kniha jízd se otevírá z odkazů a záložek; rozbitý parametr v URL nemá
shodit stránku. Obrácené období se prohodí — očividně to tak bylo
myšleno.

**Viditelnost je součástí dotazu, ne šablony.** Skryté vozidlo
(požadavek D) musí zmizet i ze součtů, z nabídky ve filtru a hlavně ze
staženého souboru. Filtrovat až při vykreslení by znamenalo, že v XLSX
bude všechno.

**Export bere celý rozsah, ne zobrazenou stránku.** Kdo si vyfiltruje
čtvrtletí, čeká čtvrtletí. Stránkování je vlastnost obrazovky, ne dat.

---

## R35 — Sloupce exportu jsou definované jednou, PDF z nich bere podmnožinu

**Rozhodnutí.** `export.COLUMNS` je jediný seznam; XLSX a CSV berou
všechny, PDF ty označené `in_pdf`.

**Proč PDF míň.** Osmnáct sloupců na šířku A4 dá písmo, které nikdo
nepřečte. PDF je na čtení a na přílohu k vyúčtování; kdo potřebuje
všechno, sáhne po XLSX.

**XLSX drží čísla jako čísla**, ne jako formátovaný text — první věc,
kterou v Excelu kdokoliv udělá, je součet sloupce ujetých km.

**CSV má středník a BOM.** Český Excel otevře čárkové CSV jako jeden
sloupec a bez BOM zobrazí diakritiku rozsypanou. Export, který se musí
před použitím opravovat, je k ničemu.

**Ujeté km se sčítají jen z ukončených jízd.** U probíhající není
konečný stav tachometru, takže by do součtu vstoupila jako nula a tiše
ho podhodnotila.

---

## R36 — Font pro PDF se bere z obrazu, ne z repozitáře

**Rozhodnutí.** PDF export hledá font se vším, co čeština potřebuje, v
tomhle pořadí: `PDF_FONT_PATH` z konfigurace → DejaVu z obrazu
(`fonts-dejavu-core`, instalovaný v Dockerfile) → Bitstream Vera od
reportlabu se složenou diakritikou.

**Proč to vůbec řešit.** Reportlab umí ze standardních fontů jen
WinAnsi a Vera, kterou přibaluje, **nemá ě ř ů ť ď ň**. České PDF by z
ní vyšlo děravé zrovna ve slovech „Přehled", „Řidič", „Účel".

**Proč ne font v Gitu.** Systémové fonty (Arial) se šířit nesmí a
750 kB binárky v repozitáři není řešení, které by chtěl někdo udržovat.
Debianí balíček je auditovatelný krok buildu, ne binárka bez původu.

**Proč fallback, a ne chyba.** Export nesmí spadnout kvůli chybějícímu
fontu. Třetí scénář vyrobí čitelné PDF s holými písmeny; je ošklivý,
ale nastane jen v prostředí bez fontu — na produkci ne.

---

## R37 — Jízda skrytého vozidla neexistuje ani přes přímý odkaz

**Rozhodnutí.** `trips/router_web.py:_load_trip` volá
`assert_vehicle_visible`, takže detail jízdy i každá akce nad ní končí
404, když uživatel nevidí vozidlo.

**Proč.** Tohle byla díra, ne nová funkce: `/trips/{id}` dosud
viditelnost vozidla nekontroloval vůbec. Kdokoliv přihlášený si mohl
přímým odkazem otevřít jízdu skrytého vozidla i s trasou, řidiči a
fotografiemi tachometru — přesně to, co požadavek D zakazuje. Našlo se
to při psaní knihy jízd, která na detail jízdy odkazuje.

**Proč v `_load_trip`.** Je to jediné místo, přes které chodí všech
devět rout nad jízdou. Kontrola v každé z nich by byla devět příležitostí
na jednu zapomenout.

---

## R38 — Preference notifikací se ukládají jako odchylky, ne jako kompletní tabulka

**Rozhodnutí.** `core.user_notification_preferences` drží jen řádky pro
volby, které uživatel skutečně uložil. **Chybějící řádek znamená
výchozí hodnotu z katalogu** (`app/core/notification_types.py`), ne
„vypnuto".

**Jak jsou tedy defaulty reprezentované.** Jako `default_enabled` u
každého typu v katalogu, tedy v kódu — ne jako data v databázi. „Nový
uživatel má rezervace zapnuté" není řádek, který by mu někdo zakládal,
ale to, že se na jeho neexistující volbu odpoví hodnotou z katalogu.
Navenek je to k nerozeznání; rozdíl je v tom, co se stane potom.

**Proč ne řádek pro každou kombinaci uživatel × typ.** Materializovaná
tabulka vypadá jednodušeji, ale má tři problémy, které se objeví až
časem:

1. Každý nový typ notifikace by znamenal migraci s backfillem přes
   všechny uživatele. Tenhle projekt počítá s tím, že typy budou
   přibývat.
2. Uživatel založený mimo aplikaci (import z Evidence nářadí, skript)
   by zůstal bez řádků, a kdyby „bez řádku" znamenalo vypnuto, tiše by
   přišel o všechno.
3. Změna výchozí hodnoty by se nedala odlišit od vědomé volby
   uživatele.

**Co se stane při přidání nového typu.** Přibude jeden záznam v `TYPES`.
Obrazovka ho vypíše sama, `create()` ho začne respektovat sama, všichni
dosavadní uživatelé dostanou jeho výchozí hodnotu okamžitě a jejich
uložené volby u ostatních typů se nehnou. Žádná migrace, žádný přepočet.

**Uloží se i volba shodná s výchozí hodnotou.** Kdo si přepínač vědomě
nechal zapnutý, nemá o své rozhodnutí přijít, kdyby se výchozí hodnota
v katalogu jednou změnila.

**Neznámý `kind` projde.** Zpráva, kterou někdo zapomene zaregistrovat,
se musí odeslat, ne zmizet — na opačnou chybu by se přišlo až tím, že
někomu nedorazí něco důležitého.

---

## R39 — Rozhoduje příjemce, ne role ani vozidlo

**Rozhodnutí.** Kontrola preference je v `notifications/service.create()`
— v jediném místě, kterým prochází každá notifikace — a ptá se na
`user_id` konkrétního příjemce.

**Proč tam.** Kdyby se kontrolovalo v jednotlivých `notify_*` helperech,
příští přidaný helper by na ni zapomněl. Takhle to nejde obejít, aniž by
někdo obešel celé zapisování notifikací.

**Vypnuté = nevznikne ani řádek ve schránce.** Kdyby se zápis udělal a
vynechal jen e-mail, znamenalo by „vypnuto" ve skutečnosti „jen bez
e-mailu" — a to uživatel u přepínače nečeká.

**Změna odpovědné osoby nic nedědí.** Příjemci se počítají z aktuálního
`vehicle.responsible_user_id` a každý se pak ptá svých vlastních voleb.
Nová odpovědná osoba tedy nepřebírá nastavení předchozí a nedostává nic
automaticky jen proto, že je odpovědná osoba.

**Administrátoři u termínů se hledají podle oprávnění
`fleet.vehicle.manage`**, ne podle názvu role — role se dají
přejmenovat a přidat, oprávnění je to, co doopravdy znamená „spravuje
celý vozový park". I administrátor si ale termíny může vypnout;
aplikace nemá žádnou cestu, jak někomu notifikaci vnutit, a nastavení
se vždycky ukládá přihlášenému uživateli, nikdy uživateli z formuláře.

---

## R40 — Potvrzení o rezervaci dostane i ten, kdo ji založil

**Rozhodnutí.** Notifikace o rezervaci (vznik, změna, zrušení) jdou
**majiteli rezervace i odpovědné osobě vozidla**, včetně případu, kdy je
majitel tím, kdo akci právě provedl.

**Proč je to změna.** Dosud platilo pravidlo „o vlastní akci se člověku
nepíše" a jediným příjemcem byla odpovědná osoba. U rezervací to ale
znamenalo, že kdo si vozidlo zamluvil, neměl žádné potvrzení — a
formulář neumí „rezervovat za někoho jiného", takže majitel je vždycky
zakladatel a bez téhle změny by nedostal nikdy nic.

**Proč je to teď v pořádku.** Námitka „e-mail o vlastním kliknutí je
šum" byla dřív hard-coded pravidlo. Teď je to volba: komu potvrzení
vadí, vypne si přepínač „Rezervace vozidel". U ostatních typů
(výpůjčky, závady, schvalování) zůstává vyřazení iniciátora beze změny.

**Dvě zprávy o jedné věci nehrozí.** Když je majitel rezervace zároveň
odpovědnou osobou vozidla, sjednotí se podle id dřív, než se cokoliv
odešle.

---

## R41 — Připomínku termínu chrání `dedupe_key`, ne paměť plánovače

**Rozhodnutí.** `dedupe_key` připomínky obsahuje vozidlo, kód termínu,
jeho datum **a stupeň naléhavosti**:
`deadline:<vehicle>:<code>:<due>:<level>`.

**Proč.** Připomínky se spouštějí denně (`scripts/send_deadline_reminders.py`
z cronu). Bez klíče by e-mail chodil každý den, dokud se termín
nevyřeší. S klíčem jen podle vozidla a termínu by se zase po prodloužení
STK už nikdy neozval.

**Datum v klíči** znamená, že nová STK = nový klíč = připomínka smí
projít znovu. **Stupeň v klíči** znamená, že přechod z oranžové na
červenou se ohlásí — do té chvíle zbývá pár dní a je to jiná informace
než „blíží se to".

**Skript sám nic neplánuje.** Opakované spouštění je práce systému, ne
aplikace; takhle jde běh kdykoliv zopakovat ručně nebo si ho prohlédnout
nanečisto (`--dry-run`), aniž by se cokoliv odeslalo.

---

## R42 — SMTP se nastavuje v aplikaci, heslo se do databáze ukládá zašifrované

**Rozhodnutí.** Server, port, přihlášení, odesílatel a STARTTLS se
nastavují v *Nastavení → Odesílání e-mailů*. Ukládají se do téže tabulky
`core.app_settings` jako prahy, jen pod vlastními klíči; `.env` zůstává
**záložním zdrojem po jednotlivých polích**.

**Proč ne druhá tabulka.** `app_settings` je obyčejné klíč/hodnota a
`get_all` i `set_values` cizí klíče ignorují, takže obě skupiny vedle
sebe žijí bez kolize. Druhé úložiště nastavení by znamenalo druhé místo,
kam se chodí dívat.

**Proč fallback po poli, ne po celé skupině.** Kdo vyplní jen server a
přihlášení, nemá tím přijít o adresu odesílatele nastavenou v `.env`.
Nevyplněné pole znamená „tohle neřeším", ne „smazat".

**Heslo se šifruje, a to je tady to podstatné.** Databáze se před každým
deployem zálohuje do `backups/postgres/*.sql`. To je obyčejný text —
heslo k firemní poště by v něm bylo čitelné, v tuctu kopií, natrvalo.
Ukládá se proto zašifrované (Fernet, klíč odvozený ze
`SESSION_SECRET_KEY`, který leží v `.env` mimo Git a do dumpu se
nedostane). Záloha tedy obsahuje jen šifrový text.

**Není to ochrana proti rootovi na serveru.** Kdo přečte `.env`, přečte
i heslo. Chrání to únik zálohy, ne server — a to je přesně ten scénář,
který u zálohovaného souboru hrozí.

**Rotace klíče se řeší, nepadá se na ní.** Když se `SESSION_SECRET_KEY`
vymění, heslo se nedá rozšifrovat: `decrypt_secret` vrátí `None`,
odesílání se zastaví se srozumitelnou hláškou a obrazovka požádá o nové
zadání. Nikdy se nepokouší přihlásit s prázdným heslem.

**Heslo se nikdy nevrací do prohlížeče**, ani zamaskované. Formulář jen
prozradí, jestli nějaké uložené je; prázdné pole znamená „nech, co tam
je". Do auditu jde `password_changed: true/false`, nikdy hodnota ani
její délka.

**Zkušební e-mail chodí jen na adresu přihlášeného administrátora.**
Adresa se schválně nebere z formuláře — jinak by z administrace byl
nástroj na rozesílání pošty komukoliv.

**Odesílání konfiguraci nečte, dostává ji.** `mailer.send_mail` přebírá
`SmtpConfig` místo sahání do `get_settings()`. Rozhodnutí, odkud se
nastavení bere, tak zůstává na jednom místě a `mailer` je jen to, co
pošle, co dostane.

---

## R43 — Odesláno je to, co dorazilo, ne to, co se zapsalo

**Rozhodnutí.** O deduplikaci připomínek rozhoduje `emailed_at`, ne
existence řádku s `dedupe_key`. Dokud je `emailed_at` prázdné, smí další
běh odeslání zopakovat. Řádek se přitom nezakládá znovu — opakuje se
doručení, ne zpráva.

**Proč se to měnilo.** Původní logika považovala připomínku za vyřízenou
ve chvíli, kdy vznikl řádek. Když v tu chvíli pošta nefungovala (a na
produkci nebyla vůbec nastavená), e-mail nedorazil **nikdy** a nikdo se
to nedozvěděl: další běh ji přeskočil jako „už posláno". Dedupe klíč má
bránit opakování, ne ztrátě.

**Stav se odvozuje, neukládá.** `email_status` je `sent` / `failed` /
`pending` spočítané z `emailed_at` a `email_error`. Třetí sloupec s touž
informací by se dřív nebo později rozešel — stejná úvaha jako u
„vypůjčeného" vozidla (R4) a nasazené sady kol (R28).

**Souběh hlídá databáze, ne aplikace**, ve dvou krocích, protože jsou to
dva různé závody:

- **zakládání** — částečný unikátní index na (`user_id`, `dedupe_key`)
  pro řádky s klíčem. Dva souběžné běhy nemůžou založit dvě stejné
  zprávy; poražený dostane `IntegrityError` v savepointu a práci
  přenechá.
- **opakování** — `SELECT … FOR UPDATE SKIP LOCKED`. Samotný index by
  tady nepomohl: oba běhy by našly existující řádek s prázdným
  `emailed_at` a oba poslali e-mail. Zámek drží po dobu odesílání ten,
  kdo ho získal, a druhý běh místo čekání přeskočí — připomínku už
  stejně někdo vyřizuje.

**`db.add` patří dovnitř savepointu.** Kdyby byl venku, zůstal by objekt
po rollbacku mezi rozepsanými a příští autoflush by tentýž INSERT zkusil
znovu — session by se tím otrávila. (Na tohle jsem při psaní narazil.)

**Bez stropu na počet pokusů.** Zkouší se dál, dokud nedorazí; je to
jednou denně a `email_attempts` říká, jak dlouho se to nedaří. Strop by
znamenal, že se připomínka po týdnu výpadku tiše vzdá — a to je přesně
ta chyba, kterou tohle rozhodnutí odstraňuje.

**Praktický důsledek.** Poštu jde nastavit až potom, co připomínky
začaly vznikat; nic se nezahodí, při nejbližším běhu odejde všechno, co
čeká.

---

## R44 — Jeden audit, ne dva: login události bydlí v téže tabulce

**Rozhodnutí.** Technické události přihlášení (`login`, `login_failed`,
`logout`) se zapisují do `core.audit_log` stejně jako změny dat, jen s
`module="auth"` a vyplněným `result`.

**Proč ne druhá tabulka.** Administrátor se ptá „co se dělo", ne „co se
dělo v tabulce A a co v tabulce B". Jedna tabulka znamená jednu
obrazovku, jeden filtr a jedno místo, kam se chodí dívat. Odlišit obojí
jde modulem — což stačí i na to, aby login události jednou dostaly
kratší retenci než business audit.

**Co audit dostal navíc.** `vehicle_id` jako vlastní sloupec (dřív se
vozidlo schovávalo uvnitř JSON payloadu a nedalo se podle něj
filtrovat), `description` pro lidsky čitelný popis a `result`.

**Vozidlo se doplní samo.** Když ho volající nepředá, vezme se z
`entity_id` (u auditu vozidla) nebo z payloadu, kde ho většina modulů
uváděla dávno předtím. Bez toho by bylo potřeba obejít přes dvacet
volajících a na některý zapomenout.

**Audit nejde vypnout.** Žádný přepínač neexistuje a nemá vzniknout.

---

## R45 — Tajemství se z auditu vyhazují centrálně, ne opatrností volajících

**Rozhodnutí.** `audit.scrub()` nahradí hodnoty u klíčů jako `password`,
`smtp_password`, `session_token` nebo `csrf_token` textem
`[odstraněno]`, a to rekurzivně, u každého zápisu.

**Proč centrálně.** Volajících je přes čtyřicet a budou přibývat. Heslo
zapsané do auditu se zpětně neodstraní — je v zálohách. Spoléhat na to,
že si každý autor dá pozor, znamená čekat, až si jednou nedá.

**Nahradit, ne smazat.** Ze záznamu má být poznat, že se to pole měnilo.

**Audit je read-only i konstrukčně.** Modul auditu nemá jedinou POST
routu ani funkci, která by řádek měnila či mazala — test to hlídá jak
na routách, tak na názvech funkcí v repository.

---

## R46 — Soukromé vozidlo je druhý rozsah téhož modelu, ne druhý model

**Rozhodnutí.** `Vehicle` dostal `vehicle_scope` (`company` | `private`)
a `owner_user_id`. Žádný paralelní model, žádný druhý modul jízd, výdajů
ani servisu.

**Není to multi-tenancy.** Jedna instalace = jedna organizace. Soukromé
vozidlo je vozidlo, které patří jednomu člověku uvnitř téže instalace.
Další firma nebo rodina dostane vlastní deployment s vlastní databází,
ne `tenant_id`.

**Celá logika je ve třech funkcích.** Viditelnost už dřív tekla přes
`can_view_vehicle` a `visible_vehicles_condition`, správa přes
`can_manage_vehicle` — deset modulů je jen volá. Rozšíření o soukromá
vozidla se proto obešlo bez zásahu do těch modulů; to je celý důvod,
proč tam ty funkce jsou.

**`visible_vehicles_condition` má výchozí rozsah `company`.** Kdo na
parametr zapomene, dostane firemní pohled — ne únik soukromých vozidel.
Fail-safe směrem k méně dat.

**Funkce nově vrací podmínku i administrátorovi.** Dřív vracela `None`
(„vidí všechno"), což by u rozsahu znamenalo, že se neuplatní.

**Čtyři kopie téže úvahy se sjednotily.** `can_manage_documents`,
`can_manage_defect` a kontrola u výdajů si „správce vozidla" počítaly
po svém a vlastník soukromého auta si k němu nemohl nahrát ani
technický průkaz. Teď všechny delegují na `can_manage_vehicle`.
Výjimkou zůstává schvalování: to je firemní proces a u soukromého
vozidla nevznikne.

---

## R47 — Soukromá vozidla se do firemních pohledů nedostanou vůbec

**Rozhodnutí.** Seznam vozidel, kalendář rezervací, kniha jízd, exporty
i přehled berou výhradně `company` — **i vlastníkovi**. Svoje soukromá
vozidla najde pod „Moje vozidla".

**Proč i vlastníkovi.** Firemní kniha jízd je podklad pro firmu. Kdyby
se do ní míchaly soukromé cesty jejího vlastníka jen proto, že používá
tentýž dashboard, přestala by být tím, čím je.

**Rezervace a schvalování soukromá vozidla odmítají**
(`assert_company_vehicle`), a to **404, ne 400**: výběr je vůbec
nenabízí, takže požadavek na ně může přijít jen ručně sestaveným POSTem.

**Cizí soukromé vozidlo = 404, ne 403** — stejně jako u skrytých
vozidel. „Existuje, ale nemáš na něj právo" je taky únik.

**Rozsah se úpravou nemění.** Kdyby šel přepnout ve formuláři, dalo by
se z firemního auta udělat soukromé i s celou historií jízd. Když to
někdy bude potřeba, je to administrativní zásah, ne políčko.

**Vlastník se u zakládání přepíše na přihlášeného**, pokud zakládající
není administrátor — podstrčené cizí id tak nemá žádný účinek.

---

## R48 — Tankování existuje i bez jízdy; tachometr posouvá i ono a servis

**Rozhodnutí.** `trip_fuelings.trip_id` je nullable. Tankování se dá
zapsat z karty vozidla, bez jakékoliv jízdy. Tabulka i model si nechávají
jméno — `vehicle_id` v nich byl denormalizovaný od začátku, takže data
byla vždycky vozidlová.

**Proč.** U části vozidel se kniha jízd nevede a eviduje se jen tankování
a servis; z nich se počítá průměrná spotřeba. Elektromobil nabíjený přes
noc v depu navíc žádnou jízdu nemá vůbec — u něj to nebyl okrajový
případ, ale ten normální, a dosud se nabíjení nedalo zapsat vůbec.

**Bez jízdy je stav tachometru povinný**, u jízdy zůstává nepovinný. Je
to jediné, z čeho se u takového vozidla dá spočítat spotřeba, a jediné,
co posune tachometr. Záznam bez něj by byl k ničemu právě tam, kde na
něm nejvíc záleží.

**Tachometr posouvá jízda, tankování i servis** — jedno pravidlo v
`app/core/odometer.py`, ne tři kopie. Bez toho by u vozidla bez knihy
jízd tachometr zamrzl na počáteční hodnotě a přestal by fungovat semafor
servisní prohlídky, který se počítá proti aktuálnímu stavu. Snížit stav
umí dál výhradně administrativní oprava se zdůvodněním (zadání 5/32);
nižší hodnota u tankování je varování k potvrzení, ale stav vozidla
nesníží.

---

## R49 — Spotřeba se počítá průměrem přes období, ne mezi dvěma tankováními

**Rozhodnutí.** `app/core/consumption.py`, čistá funkce:

```
spotřeba = (součet objemů od 2. tankování dál) / (km poslední − km první) × 100
```

**První objem se nepočítá.** To palivo bylo v nádrži ještě před prvním
odečteným stavem tachometru a k ujetým kilometrům mezi prvním a
posledním tankováním nepatří. Bez téhle úpravy vychází spotřeba
systematicky vyšší — je to celý trik za tím vzorcem.

**Proč ne „plná–plná".** Přesnější metoda potřebuje vědět, jestli se
tankovalo do plné, tedy zaškrtávátko, které budou řidiči vyplňovat
nespolehlivě. Nepřesný údaj, kterému se věří, je horší než průměr, který
se ke skutečnosti přiblíží sám.

**Číslo nese svou důvěryhodnost.** `is_reliable` je False pod tři
tankování nebo pod 100 km a obrazovka to napíše. Průměr ze dvou
tankování na padesáti kilometrech je formálně spočítaný a přitom nic
neříká.

**Jednotky se nesčítají.** Plug-in hybrid dostane dvě čísla — litry a
kWh jsou dvě různé veličiny.

**`None` znamená „zatím nevíme", ne nulu.** Nula by na kartě vozidla
vypadala jako změřená hodnota.

---

## R50 — Souhrn knihy jízd zůstává o jízdách

**Rozhodnutí.** „Za palivo při jízdách" v knize jízd počítá dál jen
tankování navázané na jízdu. Tankování mimo jízdu do něj nevstupuje.

**Proč ne jinak.** Nejdřív jsem chtěl join přepsat z `trip_id` na
`vehicle_id`, aby se započítalo obojí. To by ale udělalo kartézský
součin (každá jízda × každé tankování vozidla) a součet naopak
nafouklo — tedy horší chyba než ta, kterou to mělo opravit.

**Správná odpověď je jiná:** kniha jízd je o jízdách a její souhrn taky.
Celkové palivo za vozidlo patří do přehledu vozidla, kde se sčítá podle
`vehicle_id` a obojí zahrnuje už dnes (R27). Změnil se tedy jen popisek,
aby číslo nelhalo o tom, co znamená.

---

## R51 — OCR čte účtenku, ne tachometr, a běží doma

**Rozhodnutí.** Engine je **tesseract v kontejneru**. Čte se **jen
účtenka**; stav tachometru z fotografie se nečte
(`OCR_READ_ODOMETER=false`).

**Proč tesseract, a ne cloud.** Zdarma, bez účtu, bez kvóty a nic
neopouští server. Cloudové služby (Google Vision, Azure) dávají na
fotkách z ruky výrazně lepší výsledky, ale stojí peníze nebo aspoň
vyžadují účet s platební kartou — a bezplatné řešení je podmínka
projektu.

**Proč ne tachometr.** Tesseract je slušný na účtenku z termotiskárny:
tmavý text na světlém, rovné řádky, tištěné písmo. Digitální displej za
sklem, v odrazech a nafocený šikmo čte špatně. A u tachometru je špatný
návrh **horší než žádný**: uživatel ho potvrdí, hodnota posune tachometr
vozidla a od toho se odvíjí spotřeba i servisní intervaly. U účtenky se
špatně přečtená cena projeví hned a opraví se snadno.

**Kód pro tachometr zůstává.** `parse_odometer_text` i `read_odometer`
jsou funkční a otestované, jen je brzdí přepínač. Až bude po ruce engine,
který na displej stačí, je to jedna proměnná prostředí.

**Předzpracování je záměrně hloupé**: šedá, zvětšení pod 1000 px,
roztažení kontrastu. Chytřejší filtry pomáhají na jedné fotce a škodí na
druhé; tyhle tři pomáhají skoro vždycky.

**Tesseract běží ve vlákně.** Je blokující a trvá stovky milisekund až
sekundy — v event loopu by zdržel všechny ostatní požadavky.

**Výpadek nic neshodí.** Chybějící binárka, poškozený soubor i pád
enginu končí `None` a tankování se uloží dál, jen bez návrhu. To platilo
od začátku (zadání 14/34) a nemění se.

**Parser musí unést záměny písmen.** Při prvním skutečném běhu na
produkci tesseract přečetl `48,50 |` a `38,90 Kc/I` — tedy svislítko a
velké I místo malého „l". Text byl jinak správně, ale parser množství i
cenu za litr zahodil. Jednotka litru se proto hledá jako `[l|I1!]`, ale
jen tam, kde stojí samostatně za číslem: `48,501` zůstává číslo a
`1886,65 Kc` zůstává cena. Je to přesně ten druh chyby, který se
nedá vymyslet u stolu — našel ho až obrázek prohnaný skutečným enginem.

**Česká účtenka píše popisek před číslo.** Když přišla první skutečná
účtenka, ukázalo se, že tesseract ji přečetl dobře — `Litry : 0047.45`,
`Celkem: 01826,80 Kč` — ale parser z ní vzal jen datum a částku. Byl
totiž postavený na tvaru `48,50 l`, který jsem si vymyslel v testovacím
obrázku. Hledá se proto obojí: `popisek : hodnota` i `hodnota jednotka`.

**Cena za jednotku se dopočítává.** Na účtence stojí „Kč/l 38,50" a
tesseract z toho udělal `Ke “1 38,50`; na to se rozumný vzor napsat
nedá. Podíl celkové ceny a množství vyjde stejně a u slevy dokonce
správněji — je to skutečně zaplacená cena za litr. Když je údaj na
účtence čitelný, má přednost.

**Poučení pro příště:** syntetický testovací obrázek ověřil jen to, že
parser rozumí sám sobě. Skutečný podklad ukázal chybu během minuty.

**Co ukázalo sedm skutečných účtenek.** Tři chyby, které syntetický
obrázek nemohl odhalit:

- `0057, 46` se četlo jako **57** místo 57,46 — mezera za desetinnou
  čárkou. Nejhorší druh chyby: výsledek vypadal věrohodně a uživatel by
  ho potvrdil.
- `22,10,2023` — tečka a čárka jsou na tisku k nerozeznání, takže
  oddělovačem data smí být obojí a klidně pokaždé jiné.
- `elkem:` — tesseract ztratil první písmeno u „Celkem" a celá částka
  zmizela.

**Název stanice se odhaduje, ale opatrně.** Nejdřív známé sítě
(Benzina, ORLEN, Shell…) kdekoliv v textu — z těch vyjde čisté jméno
místo toho, co z loga zbylo. Jinak první řádek hlavičky, který vypadá
jako firma: má velké písmeno, není to adresa a neobsahuje slova jako
„Stojan" nebo „Litry". Když se hlavička nepřečetla, **nevrátí se nic** —
nabídnout „Stojan: 1 Nafta" jako čerpací stanici je horší než prázdné
pole, protože to uživatel jen tak nepřepíše.

**Samotný název stanice nestačí na nabídku.** Je to odhad z hlavičky;
bez jediného čísla se uživateli nic neukazuje.
