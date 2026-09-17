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
