# Zadání projektu – Kniha jízd / Evidence vozidel

## 1. Cíl projektu

Vytvořit samostatnou webovou aplikaci **Kniha jízd / Evidence vozidel**, která bude fungovat podobným způsobem jako stávající aplikace **Evidence nářadí**.

Aplikace je určena především pro používání v terénu na mobilním telefonu. Řidič musí být schopen vozidlo rychle najít nebo načíst QR kódem, zahájit výpůjčku, po jízdě ji ukončit a zadat pouze nezbytné údaje.

Na počítači bude aplikace sloužit především pro administraci vozidel, servisů, dokumentů, rezervací, závad, uživatelů a kompletní knihy jízd.

### Zásadní principy

- mobil-first UX
- jednoduchost a minimum povinných polí
- QR kód na každém vozidle
- žádný kontinuální GPS tracking
- žádná složitá telematika
- tachometr je hlavní zdroj skutečně ujetých kilometrů
- možnost **vyfotit stav tachometru místo ručního zadání**
- fotografie účtenek a dokumentů
- připravenost na OCR, ale OCR nesmí automaticky bez kontroly měnit účetní/knižní údaje
- historie všech důležitých změn
- podobný vzhled, komponenty, autentizace, oprávnění, práce s fotografiemi a deployment jako Evidence nářadí
- **žádné propojení s DSS**

---

# 2. Vztah k existujícím projektům

Před zahájením implementace Claude Code musí projít existující projekt:

`evidence_naradi`

a převzít z něj vhodné:

- architektonické principy
- autentizaci
- uživatele a oprávnění
- UI komponenty
- mobilní layout
- QR funkcionalitu
- upload a zpracování fotografií
- CSRF ochranu
- HTMX vzory, pokud je projekt používá
- databázové konvence
- testovací strukturu
- Docker / Docker Compose
- deployment postup
- nginx konfiguraci
- logging
- zálohování

Neopisovat slepě implementaci. Pokud je pro Knihu jízd vhodnější jiné řešení, použít čistší řešení.

### Důležité

Kniha jízd je **samostatná aplikace**.

Nesmí vzniknout:

- vazba na databázi DSS
- import zakázek z DSS
- API závislost na DSS
- společné business entity s DSS

Případné sdílení technických komponent nebo autentizačního mechanismu je možné pouze pokud odpovídá architektuře Evidence nářadí a nezpůsobí těsnou vazbu na DSS.

---

# 3. Deployment

Aplikace bude provozována na stávajícím VPS.

Produkční URL bude přístupná přes:

`https://solareg.azunimb.cz`

Na hlavním rozcestníku musí vzniknout položka:

**Kniha jízd**

Kliknutí otevře aplikaci Kniha jízd.

Použít stejný princip reverse proxy a deploymentu jako u Evidence nářadí.

Aplikace nesmí vystavovat databázi ani interní port přímo do internetu.

---

# 4. Uživatelé a oprávnění

## Běžný uživatel / řidič

Může:

- zobrazit vozidla
- zahájit výpůjčku
- ukončit vlastní výpůjčku
- rezervovat vozidlo
- zobrazit své rezervace
- zadat / opravit údaje vlastní jízdy před uzavřením
- nahlásit závadu
- přidat fotografii závady
- zadat tankování
- vyfotit účtenku
- vyfotit tachometr

Nemůže měnit základní konfiguraci vozidla, servisní historii nebo dokumenty, pokud mu to nebylo výslovně povoleno.

## Odpovědná osoba vozidla

Odpovědná osoba je správcem konkrétního vozidla.

Má přístup ke všem informacím daného vozidla a může:

- upravovat údaje vozidla
- spravovat dokumenty
- spravovat servisní údaje
- přidávat servisní úkony
- řešit závady
- měnit jejich stav
- spravovat fotografie
- kontrolovat historii jízd
- dostávat všechny relevantní notifikace k vozidlu

## Administrátor

Má úplný přístup ke všem vozidlům, jízdám, rezervacím, servisům, závadám, dokumentům, notifikacím a nastavení aplikace.

---

# 5. Vozidlo

Každé vozidlo bude mít vlastní kartu.

Minimální údaje:

- interní identifikátor
- SPZ
- značka
- model
- VIN
- rok výroby
- typ vozidla
- odpovědná osoba
- aktivní / neaktivní
- QR token

Další údaje lze přidávat podle potřeby.

## Stav vozidla

- aktuální stav km
- aktuální stav nádrže
- poslední známý stav
- poslední aktualizace

Stav km se má automaticky aktualizovat z uzavřených jízd, případně z administrativního záznamu.

Nikdy nesmí být možné běžnou jízdou nastavit konečný stav km nižší než předchozí platný stav bez explicitního administrativního zásahu.

---

# 6. QR kód

Každé vozidlo má unikátní QR kód.

Po jeho načtení se uživateli otevře přímo karta konkrétního vozidla a nabídne se:

**Zahájit výpůjčku**

QR nesmí obsahovat citlivé údaje. Používat náhodný/neodhadnutelný token.

QR musí být možné:

- zobrazit v administraci
- vytisknout
- stáhnout / vytisknout jako štítek

---

# 7. Informace při příchodu k vozidlu

Po načtení QR nebo výběru vozidla musí uživatel před zahájením jízdy vidět:

## Základní stav

- stav km z poslední jízdy
- stav nádrže
- poslední jízdu
- poslední poznámky
- aktuální nevyřešené závady

## Povinné termíny

Výrazně zobrazit:

- STK
- dálniční známku
- výměnu oleje

Použít semafor:

- 🟢 zelená – dostatečná rezerva
- 🟠 oranžová – blíží se termín
- 🔴 červená – po termínu / nutno řešit

Prahové hodnoty musí být konfigurovatelné administrátorem.

---

# 8. Zahájení výpůjčky

Po stisknutí **Zahájit výpůjčku** se automaticky uloží:

- vozidlo
- přihlášený uživatel
- datum a čas zahájení
- počáteční stav km
- počáteční stav nádrže

## Stav tachometru

Preferovaný způsob zadání:

1. uživatel vyfotí tachometr
2. aplikace se pokusí údaj OCR přečíst
3. zobrazí rozpoznanou hodnotu
4. uživatel ji potvrdí nebo opraví

Současně se uloží fotografie jako důkazní podklad.

Musí existovat i jednoduchá možnost:

**Zadat stav km ručně**

OCR je pomocník, ne autoritativní zdroj.

Pokud OCR není dostupné nebo selže, aplikace musí normálně fungovat dál.

---

# 9. Rezervace

Musí existovat samostatný rezervační systém.

Uživatel rezervuje:

- vozidlo
- datum a čas od
- datum a čas do
- účel / poznámku

Volitelně:

- předpokládaná trasa
- očekávaný počet km

## Kalendář

Hlavní stránkou rezervací má být přehledný kalendář.

Musí být dobře použitelný:

- na mobilu
- na tabletu
- na PC

Kalendář musí jasně rozlišovat:

- volné termíny
- rezervace
- právě probíhající výpůjčku
- servis / mimo provoz

Volný termín musí být na první pohled zřejmý.

---

# 10. Výpůjčka rezervovaného vozidla

Pokud uživatel zahajuje výpůjčku v době, kdy je vozidlo rezervované jiným uživatelem, aplikace nesmí výpůjčku automaticky zablokovat.

Musí však zobrazit výrazné upozornění:

> Vozidlo je v tomto termínu rezervováno jiným uživatelem.

Zobrazit:

- jméno rezervujícího
- začátek
- konec rezervace

A vyžadovat potvrzení:

**Zpět**

**Přesto zahájit výpůjčku**

Potvrzení uložit do historie.

---

# 11. Ukončení jízdy

Po návratu uživatel stiskne:

**Ukončit výpůjčku**

Povinné údaje:

- konečný stav km
- konečný stav nádrže
- účel jízdy
- trasa

Volitelné:

- další řidiči
- poznámka
- tankování
- závada
- fotografie
- další údaje

## Konečný stav tachometru

Stejně jako při startu:

- možnost vyfotit tachometr
- OCR pokus
- zobrazení rozpoznané hodnoty
- potvrzení uživatelem
- možnost ruční opravy

Fotografie tachometru se uloží k jízdě.

---

# 12. Řidiči

Primární řidič je automaticky přihlášený uživatel.

U jízdy lze přidat další řidiče.

Historie musí umožnit zjistit:

- kdo výpůjčku zahájil
- kdo ji ukončil
- kdo byl uveden jako další řidič

---

# 13. Účel jízdy

Účel je povinný, ale jednoduchý.

Preferovat:

- předdefinované typy účelu
- možnost vlastního textu

Například:

- servis
- montáž
- doprava materiálu
- schůzka
- služební cesta
- jiný

Nepřidávat zbytečná povinná pole.

---

# 14. Trasa

Uživatel zadá trasu jednoduchým způsobem.

Například:

`Mladá Boleslav – Zlatá Olešnice – Mladá Boleslav`

První verze nemusí používat GPS tracking.

## Kontrola vzdálenosti

Aplikace se pokusí podle mapových dat spočítat orientační silniční vzdálenost zadané trasy.

Porovná:

- očekávanou vzdálenost podle mapy
- skutečně ujeté km podle tachometru

Pokud rozdíl překročí 20 %, zobrazit upozornění.

Například:

> ⚠️ Ujeto 276 km, očekávaná vzdálenost trasy je přibližně 250 km. Rozdíl: +10 %.

Při překročení:

> ⚠️ Ujetá vzdálenost se výrazně liší od očekávané vzdálenosti trasy (+28 %).

Jízdu neblokovat.

Uživatel může uvést vysvětlení:

- objížďka
- více zastávek
- změna trasy
- jiný důvod

### Důležité

Neimplementovat:

- kontinuální GPS tracking
- historii GPS bodů
- sledování pohybu vozidel
- telematiku
- permanentní lokalizaci zaměstnanců

Mapová funkce slouží pouze jako orientační kontrola zadané trasy.

---

# 15. Tankování

Tankování je součástí jízdy.

Povinné:

- datum
- množství litrů

Volitelné:

- cena
- cena za litr
- čerpací stanice
- stav km
- druh paliva
- fotografie účtenky
- poznámka

## Účtenka

Uživatel může vyfotit účtenku.

Do budoucna implementovat OCR:

- datum
- litrů
- cena/litr
- celková cena
- čerpací stanice
- případně stav km

OCR výsledek se vždy zobrazí uživateli ke kontrole.

Teprve po potvrzení se uloží jako strukturovaná data.

---

# 16. Závady

Uživatel může při zahájení i ukončení jízdy nahlásit závadu.

Závada obsahuje:

- vozidlo
- datum
- uživatel
- popis
- priorita
- stav
- fotografie

Stavy:

- nová
- řeší se
- vyřešena

Priorita například:

- nízká
- běžná
- vysoká
- kritická

Kritická závada může zobrazit výrazné upozornění dalšímu uživateli při pokusu o výpůjčku.

Závady musí zůstat v historii i po vyřešení.

---

# 17. Servisní historie

Každé vozidlo má kompletní historii servisních úkonů.

Servisní záznam:

- datum
- stav km
- popis úkonu
- servis / dodavatel
- cena
- poznámka
- fotografie faktury
- další přílohy

Příklady:

- výměna oleje
- filtry
- brzdy
- pneumatiky
- oprava
- pravidelný servis
- STK
- jiné

## Servisní intervaly

Vozidlo musí umožňovat nastavit:

- poslední výměnu oleje
- stav km při výměně
- interval v km
- interval v měsících

Systém automaticky vypočítá další termín.

---

# 18. Dokumenty vozidla

Ke každému vozidlu musí být možné uložit a zobrazit dokumenty:

- TP
- OTP
- zelená karta
- další dokumenty

Dokument může být:

- PDF
- obrázek
- případně jiný vhodný formát

U dokumentů evidovat:

- typ
- název
- datum nahrání
- případně platnost od/do
- poznámku

Citlivé dokumenty nesmí být veřejně dostupné přes odhadnutelnou URL.

---

# 19. STK a dálniční známka

Vozidlo má:

- platnost STK
- platnost dálniční známky

Systém musí:

- zobrazovat termín
- počítat zbývající dobu
- zobrazovat semafor
- posílat upozornění odpovědné osobě

Prahy upozornění musí být konfigurovatelné.

---

# 20. Notifikace

## Odpovědná osoba dostává

### Rezervace

Informaci o rezervaci jejího vozidla.

### Výpůjčka

Informaci o zahájení výpůjčky.

### Vrácení

Informaci o ukončení výpůjčky.

### Závada

Informaci o nahlášené závadě.

### Servis

Upozornění na blížící se servis.

### STK

Upozornění před koncem STK.

### Dálniční známka

Upozornění před koncem platnosti.

### Olej

Upozornění podle km / času.

Konkrétní intervaly upozornění musí být konfigurovatelné.

---

# 21. Dashboard

Administrace má přehledný dashboard.

Například:

**Vozidla**
- celkem
- aktivní
- vypůjčená
- v servisu
- s kritickou závadou

**Termíny**
- STK
- dálniční známky
- olej
- servis

**Rezervace**
- dnešní
- aktuální
- nadcházející

**Závady**
- nové
- kritické
- řešené

---

# 22. Kniha jízd

Samostatná hlavní sekce.

Filtrování:

- období
- vozidlo
- řidič
- účel
- případně stav jízdy

Záznam obsahuje minimálně:

- datum
- vozidlo
- primární řidič
- další řidiči
- čas startu
- čas konce
- počáteční km
- konečné km
- ujeté km
- počáteční nádrž
- konečná nádrž
- účel
- trasu
- tankování
- poznámky
- závady

Musí být možné zobrazit detail celé jízdy včetně fotografií tachometru a účtenek.

---

# 23. Export

Připravit export knihy jízd minimálně do:

- XLSX
- CSV
- PDF

Export musí respektovat aktivní filtry.

---

# 24. Fotografie a soubory

Fotografie musí být zpracovávány obdobně jako v Evidence nářadí.

Požadavky:

- bezpečný upload
- kontrola typu souboru
- omezení velikosti
- automatické zmenšení fotografií
- zachování dostatečné kvality pro čtení tachometru a účtenek
- bezpečné názvy souborů
- žádné veřejné náhodně dostupné soubory
- autorizovaný přístup

Originál lze uchovat pouze pokud je to z hlediska kapacity a bezpečnosti odůvodněné.

---

# 25. OCR

OCR používat jako pomocnou funkci pro:

1. tachometr
2. účtenku za tankování

OCR nesmí být kritickou závislostí aplikace.

Pokud OCR selže:

- uživatel pokračuje ručním zadáním.

OCR výsledek musí být vždy potvrzen uživatelem.

Před uložením kontrolovat logickou správnost.

Například:

- konečný stav km nesmí být menší než počáteční
- konečný stav km nesmí být výrazně mimo aktuální stav vozidla bez potvrzení
- množství paliva musí být v rozumném rozsahu

---

# 26. Datový model

Minimálně navrhnout entity:

- `User`
- `Vehicle`
- `VehicleAssignment`
- `VehicleDocument`
- `VehicleService`
- `VehicleDefect`
- `VehiclePhoto`
- `VehicleReservation`
- `VehicleTrip`
- `TripDriver`
- `TripFueling`
- `TripPhoto`
- `TripNote`
- `Notification`

Přesný model upravit podle architektury Evidence nářadí.

## Historie

U důležitých údajů zachovat audit/historii.

Minimálně:

- vytvoření
- změna
- kdo změnu provedl
- datum a čas

---

# 27. Stavový automat jízdy

Jízda musí mít jasný životní cyklus:

`REZERVOVÁNO`

→ `ACTIVE`

→ `COMPLETED`

Případně:

`CANCELLED`

Administrátor může řešit výjimečné případy.

Po uzavření jízdy nesmí běžný uživatel libovolně měnit historické údaje.

Pokud je potřeba oprava, evidovat ji auditně.

---

# 28. UX – mobil

Mobilní používání je prioritní.

Typický scénář:

### Řidič přijde k autu

1. načte QR
2. vidí stav vozidla
3. vidí STK / známku / olej
4. vidí případné závady
5. klikne Zahájit
6. vyfotí tachometr nebo potvrdí předvyplněný údaj
7. potvrdí stav nádrže
8. jede

### Po návratu

1. otevře aktivní výpůjčku
2. vyfotí tachometr
3. zadá/potvrdí km
4. zadá nádrž
5. vybere účel
6. zadá trasu
7. případně tankování
8. případně závadu
9. klikne Ukončit

Běžná jízda musí být zvládnutelná s minimem kliknutí.

---

# 29. UX – desktop

Na PC má být důraz na:

- dashboard
- správu vozidel
- kalendář
- knihu jízd
- servis
- závady
- dokumenty
- notifikace
- uživatele
- exporty

Desktop rozhraní může být informačně bohatší než mobilní rozhraní.

---

# 30. Bezpečnost

Dodržet bezpečnostní principy Evidence nářadí.

Minimálně:

- autentizace
- autorizace
- CSRF ochrana
- validace vstupů
- bezpečný upload
- kontrola oprávnění při každém přístupu k dokumentům/fotografiím
- bezpečné QR tokeny
- auditní log
- ochrana před IDOR
- bezpečné ukládání secrets
- žádné secrets v Git repozitáři

---

# 31. Testy

Před produkčním nasazením musí existovat testy minimálně pro:

### Vozidla

- vytvoření
- úprava
- deaktivace
- odpovědná osoba

### QR

- správné vozidlo
- neplatný token
- nepovolený přístup

### Výpůjčka

- start
- stop
- správné km
- správné časy
- nemožný stav km

### Rezervace

- vytvoření
- změna
- zrušení
- překryv
- výpůjčka během cizí rezervace
- potvrzení výpůjčky přes varování

### Tankování

- povinná pole
- fotografie
- OCR výsledek
- potvrzení OCR

### Závady

- vytvoření
- fotografie
- změna stavu
- upozornění

### Servis

- vytvoření
- cena
- km
- faktura
- výpočet dalšího servisu

### Dokumenty

- upload
- zobrazení
- oprávnění
- odstranění/náhrada

### Notifikace

- rezervace
- start
- konec
- závada
- STK
- známka
- olej

### Kniha jízd

- filtry
- výpočet km
- export
- historie

---

# 32. Validace dat

Aplikace musí chránit proti zjevným chybám.

Například:

- konečný km < počáteční km → chyba
- stav km vozidla se vrací zpět → chyba / administrativní výjimka
- tankování záporné nebo nesmyslné → chyba
- rezervace s koncem před začátkem → chyba
- servisní km mimo logický rozsah → upozornění

Při podezřelých hodnotách preferovat:

**Upozornit a vyžádat potvrzení**

před tvrdým blokováním, pokud nejde o jednoznačně neplatná data.

---

# 33. Mapová služba

Mapová kontrola trasy musí být implementována jako samostatná služba/vrstva.

Nezavazovat celý systém ke konkrétnímu poskytovateli map.

Implementace má umožnit pozdější výměnu poskytovatele.

Pro první verzi stačí:

- geokódování míst
- výpočet orientační silniční vzdálenosti
- porovnání s tachometrem
- upozornění při rozdílu > 20 %

Žádný tracking.

---

# 34. Co není součástí první verze

Do první verze NEPATŘÍ:

- GPS tracking vozidel
- kontinuální sledování zaměstnanců
- OBD/telematika
- automatické čtení CAN bus
- napojení na GPS jednotky
- automatické zjišťování skutečné trasy
- propojení s DSS
- automatické účetní zaúčtování tankování
- automatické schvalování faktur

Architektura ale může být připravena na budoucí rozšíření.

---

# 35. Doporučené pořadí implementace

## Etapa 1 – základ

- projekt
- autentizace
- uživatelé
- vozidla
- odpovědné osoby
- QR
- základní mobilní UI
- dashboard

## Etapa 2 – výpůjčky

- zahájení
- ukončení
- km
- nádrž
- tachometr
- fotografie
- účel
- trasa
- další řidiči

## Etapa 3 – rezervace

- kalendář
- volné termíny
- rezervace
- konflikt rezervace
- varování při výpůjčce

## Etapa 4 – závady

- závady
- fotografie
- stav
- priority
- upozornění

## Etapa 5 – tankování

- tankování
- účtenky
- fotografie
- OCR infrastruktura

## Etapa 6 – servis a dokumenty

- servisní historie
- faktury
- STK
- dálniční známky
- olej
- dokumenty vozidla

## Etapa 7 – notifikace

- emaily
- servis
- STK
- dálniční známky
- olej
- rezervace
- výpůjčky
- vrácení
- závady

## Etapa 8 – kniha jízd a export

- filtry
- statistiky
- XLSX
- CSV
- PDF

## Etapa 9 – mapová kontrola

- geokódování
- výpočet trasy
- porovnání
- upozornění >20 %

## Etapa 10 – produkce

- Docker
- nginx
- VPS
- zálohy
- monitoring
- smoke test
- aktualizace rozcestníku solareg.azunimb.cz

---

# 36. Definition of Done

Projekt je považován za hotový, pokud:

- aplikace běží na VPS
- je dostupná z rozcestníku solareg.azunimb.cz
- běžný uživatel zvládne celou jízdu z mobilu
- vozidlo lze najít QR kódem
- stav tachometru lze zadat i vyfotit
- existuje historie jízd
- existuje kalendář rezervací
- rezervované vozidlo zobrazí při výpůjčce varování
- fungují odpovědné osoby
- fungují závady a fotografie
- funguje servisní historie
- lze ukládat faktury
- lze zobrazit dokumenty vozidla
- fungují STK / dálniční známka / olej se semaforem
- fungují emailové notifikace
- funguje tankování
- fungují fotografie účtenek
- existuje OCR workflow s potvrzením uživatele
- existuje kniha jízd
- fungují filtry a exporty
- funguje orientační kontrola trasy
- neexistuje GPS tracking
- nejsou vytvořeny žádné závislosti na DSS
- testy pro hlavní workflow procházejí
- proběhne produkční smoke test

---

# 37. Pokyn pro Claude Code

Claude Code má po převzetí tohoto zadání:

1. projít existující `evidence_naradi`
2. projít jeho README, CLAUDE.md, deployment skripty, Docker konfiguraci, testy a relevantní zdrojové soubory
3. zjistit aktuální architekturu a použité technologie
4. nevytvářet paralelní nebo zbytečně odlišný technologický stack
5. vytvořit novou samostatnou aplikaci Kniha jízd podle zavedených konvencí
6. implementovat projekt postupně podle etap výše
7. po každé významné etapě spustit testy
8. nepřidávat funkce mimo zadání bez odůvodnění
9. zachovat jednoduché mobilní používání
10. neblokovat základní provoz kvůli OCR nebo mapové službě
11. neimplementovat GPS tracking ani telematiku
12. nepropojovat aplikaci s DSS
13. po dokončení provést produkční deployment a smoke test

Při implementaci preferovat jednoduché, udržovatelné řešení před předčasnou komplexitou.

Pokud je některý detail v zadání nejednoznačný, Claude Code má nejprve zkontrolovat konvence Evidence nářadí a zvolit řešení, které je s ní konzistentní. Pokud jde o rozhodnutí s dopadem na datový model nebo bezpečnost, má jej zaznamenat do dokumentace projektu.
