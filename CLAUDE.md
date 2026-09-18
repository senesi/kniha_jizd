# Kniha jízd / Evidence vozidel – kontextový dokument

> Tento soubor čte Claude Code automaticky při startu v této složce.
> Doplňuje globální `~/.claude/CLAUDE.md` (sdílený VPS, obecná
> bezpečnostní pravidla) o specifika tohoto projektu. Nadřazená pravidla
> z globálního souboru platí i tady a nejsou zde opakovaná.
>
> Zadání projektu je v `zadání_projektu.md` — ten je závazný. Tenhle
> soubor popisuje, **jak** je projekt postavený a provozovaný.

## 1. Identita projektu

- **Kniha jízd** — samostatná aplikace platformy SOLAREG pro evidenci
  vozidel a jízd: vozidla, QR kódy, výpůjčky, rezervace, závady, servis,
  dokumenty, tankování/nabíjení, kniha jízd a exporty.
- Vzorem konvencí je **Evidence nářadí** (`C:\Claude\evidence_naradi`) —
  stejný stack, stejné principy autentizace, oprávnění, práce s
  fotografiemi, Docker i deploy. Není to sdílený kód, jen sdílené
  konvence.
- **Žádná vazba na DSS.** Vlastní databáze, vlastní přihlašování, vlastní
  session cookie, vlastní úložiště. Žádné společné tabulky ani API.
- Produkční doména/cesta: `https://solareg.azunimb.cz/kniha-jizd/`
  (subcesta na stejné doméně jako Evidence nářadí, nginx propouští cestu
  beze změny). Na rozcestníku musí přibýt položka **Kniha jízd**.

## 2. Stav rozpracovanosti

Implementace jde po etapách podle `zadání_projektu.md`, kapitola 35.
Aplikace musí být spustitelná po **každé** etapě, ne až na konci.

| Etapa | Obsah | Stav |
|---|---|---|
| 1 | projekt, autentizace, uživatelé, vozidla, odpovědné osoby, QR, mobilní UI, dashboard | **hotovo** |
| 2 | výpůjčky (start/konec, km, nádrž, tachometr, foto, účel, trasa, další řidiči) | **hotovo** |
| 3 | rezervace a kalendář | **hotovo** |
| 4 | závady | **hotovo** |
| 5 | tankování / nabíjení, účtenky, OCR infrastruktura | **hotovo**; chybí jen napojení skutečného OCR enginu (`OCR_PROVIDER=none`) |
| 6 | servis a dokumenty vozidla | **hotovo** |
| 6b | kola/pneumatiky a výdaje vozidla | **hotovo** (nad rámec původních etap) |
| 7 | e-mailové notifikace | `app/core/mailer.py` + `modules/notifications` hotové, chybí plánované připomínky termínů |
| 8 | kniha jízd, filtry, exporty XLSX/CSV/PDF | neimplementováno |
| 9 | mapová kontrola trasy | neimplementováno (konfigurace připravená, `MAPS_PROVIDER=none`) |
| 10 | produkce (Docker, nginx, VPS, zálohy, smoke test) | **nasazeno a běží**; zbývá položka v rozcestníku a pravidelné zálohy |

Datový model (`app/models/fleet.py`) je navržený pro všechny etapy
najednou, aby pozdější etapy nepotřebovaly přestavbu schématu. Migrace
`0001` proto zakládá i tabulky, které zatím nemá kdo plnit.

Routery neexistujících etap **nejsou** v `app/main.py` — přidávají se
tam, až modul vznikne.

## 3. Produkce

- Adresář projektu na VPS: `/opt/kniha_jizd/`. **Nasazeno 17. 9. 2026**, běží na <https://solareg.azunimb.cz/kniha-jizd/>.
- Kontejnery: `kniha-jizd-app` (`127.0.0.1:8002` → 8000) a
  `kniha-jizd-postgres` (bez mapovaného portu).
- Databáze: PostgreSQL, produkční databáze `kniha_jizd`.
- Persistentní data:
  - `/opt/kniha_jizd/data/photos` — fotografie (tachometr, účtenky,
    závady, vozidla, faktury),
  - `/opt/kniha_jizd/data/documents` — dokumenty vozidel (TP, OTP, …).
- Databáze ani interní port se nikdy nevystavují do internetu.
- Ostatní projekty na VPS (DSS, Evidence nářadí) se deployem tohoto
  projektu nesmí dotknout.

## 4. Git / deploy

- Workflow: `localhost → testy → git commit → git push → ruční deploy na
  VPS`. Push neznamená automatický deploy.
- **Přístup na VPS a celý deploy postup je v `docs/SERVER_SETUP.md`** —
  SSH alias, izolace ostatních projektů, struktura na serveru, nginx,
  co ověřit po nasazení. Při práci se serverem začni tam.
- Ve zkratce: alias `netcup-fve`, SSH výhradně přes **PowerShell**
  (Git Bash nemá `SSH_AUTH_SOCK` napojený na Windows ssh-agent a skončí
  na „Permission denied (publickey)"). Klíč má passphrase odemčenou ve
  službě `ssh-agent`; když služba neběží, SSH selže — spustí ji uživatel.
- Deploy skript: `scripts/deploy_vps.sh` — čistý working tree →
  `git pull --ff-only` → **záloha databáze** → build image → migrace →
  restart *pouze* aplikačního kontejneru → kontrola `/healthz`.

## 5. Lokální vývoj

- Python 3.12+, FastAPI, SQLAlchemy 2 (async), Alembic, Jinja2, pytest.
- Databáze: `docker/docker-compose.dev.yml` → kontejner
  `kniha-jizd-postgres-dev`, port `127.0.0.1:5435`, databáze
  `kniha_jizd_dev`. Port 5435 schválně — 5434 patří Evidenci nářadí.
- Spuštění:
  ```bash
  docker compose -f docker/docker-compose.dev.yml up -d
  .venv/Scripts/python -m alembic upgrade head
  ADMIN_EMAIL=... ADMIN_FULL_NAME="..." .venv/Scripts/python -m scripts.create_admin
  .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8100
  ```
  Aplikace běží napevno pod cestou `/kniha-jizd/` — stejně lokálně i na
  produkci, liší se jen port a doména.
- Testy: `.venv/Scripts/python -m pytest tests -q`. Vlastní jednorázová
  databáze `kniha_jizd_test` na tomtéž dev kontejneru; nikdy
  `kniha_jizd_dev`, nikdy VPS. Schéma se do ní nalévá skutečnou migrací,
  takže testy zároveň hlídají, že migrace odpovídá modelům.
- Před commitem vždy celý test suite.

## 6. Architektura

```
app/
  core/        průřezové věci: config, db, deps (oprávnění), csrf, audit,
               photos, documents, previews (miniatury dokumentů),
               mailer, app_settings, fleet_status
               (semafor), fuel (l vs. kWh), ocr (pomůcka, ne závislost),
               labels, flash, templates
  models/      core.py (uživatelé, role, audit, nastavení)
               fleet.py (celá doména vozidel)
  modules/     <modul>/{router_web,service,repository,schemas}.py
               hotové: auth, dashboard, vehicles, trips, reservations,
               defects, approvals, fuelings, services, documents,
               wheels, expenses, users, notifications, settings
  templates/   Jinja2, mobile-first, Tailwind přes CDN
  static/      vendorované JS (qr-scanner), favicony
```

- **Vrstvy:** router řeší HTTP a oprávnění, service business pravidla a
  audit, repository dotazy. Router nesahá na model přímo tam, kde už
  existuje service.
- **Schémata v databázi:** `core` (uživatelé, role, oprávnění, audit,
  nastavení) a `fleet` (vozidla a vše kolem nich).
- **Cesty:** všechny routy, redirecty i odkazy v šablonách mají prefix
  `/kniha-jizd` napsaný doslova. `/healthz` je bez prefixu (chodí na něj
  jen docker healthcheck zevnitř kontejneru).

## 7. Oprávnění

Role: `admin`, `odpovedna_osoba`, `user` (řidič). Zakládá je migrace
`0001` spolu s oprávněními.

- Rozsah „jen moje vozidla" **nedělá role**, ale porovnání oprávnění
  `fleet.vehicle.manage.own` proti `vehicle.responsible_user_id` — viz
  `app/core/access.py:assert_vehicle_manage_access`. Volá se až po
  načtení vozidla; route dependency to udělat nemůže, protože běží dřív,
  než se cokoliv načte.
- `require_any_permission(...)` je jen hrubý filtr („nemá ani jedno z
  toho"). Rozhodnutí o konkrétním vozidle vždy patří do těla routy.
- Skrytí tlačítka v šabloně není kontrola oprávnění.

## 7a. Viditelnost vozidla

Vozidlo má `visibility`: `all` (vidí každý) nebo `restricted` (jen
odpovědná osoba a administrátor). Pro ostatní vozidlo **neexistuje** —
neobjeví se v seznamu, na přehledu, ve výběru, v kalendáři, v závadách
ani po načtení QR kódu.

- Výpisy filtruje `visible_vehicles_condition(codes, user)` přidaná do
  dotazu, ne až šablona.
- Jednotlivé vozidlo hlídá `assert_vehicle_visible(...)`, která vrací
  **404, ne 403** — u skrytého vozidla je i informace „existuje, ale
  nemáš právo" únikem, a přes QR token by šlo ověřovat, která nálepka
  patří kterému autu.
- `SEE_ALL_CODES` obsahuje jen `fleet.vehicle.manage`. Schválně tam
  **není** `fleet.logbook.view` — to je oprávnění ke knize jízd, ne k
  obcházení viditelnosti, a má ho i odpovědná osoba.

Nová routa, která pracuje s vozidlem, musí projít jedním z těch dvou.

## 7b. Validace: kdy blokovat a kdy se zeptat

Zadání 32 chce raději upozornit a vyžádat potvrzení než tvrdě blokovat.
V kódu jsou to dvě různé výjimky (`app/modules/trips/service.py`):

- `TripError` — jednoznačně neplatný vstup, neuloží se nikdy (konečný km
  nižší než počáteční, stav km jdoucí zpět, chybějící trasa nebo účel).
- `TripWarning` — podezřelé, ale možná správné. Nese kód, který se
  uživateli zobrazí jako zaškrtávátko; teprve když ho pošle zpátky
  (`confirm=<kód>`), akce projde. Každé potvrzení se ukládá do auditu,
  takže je z historie vidět, že to člověk viděl a rozhodl.

Nové varování se přidává vyhozením `TripWarning` — formulář ho vykreslí
sám, není potřeba sahat do šablony. Dosud existují: `vehicle_status`,
`critical_defect`, `reservation`, `odometer_jump`, `trip_overlap`, `ocr`.

Výjimkou je `TripApprovalRequired` (potomek `TripError`): chybějící
schválení se neodklikává, jízda prostě nevznikne.

## 8. Bezpečnostní zásady projektu

- Každý POST má `verify_csrf` (výjimka: `/login` — tam ještě není co
  chránit).
- Fotografie ani dokumenty **nejsou** servírované staticky. Každé stažení
  jde přes autorizovanou routu (`/attachments/{id}/{variant}`), soubory
  na disku mají náhodná UUID jména.
- QR token je náhodný (`secrets.token_urlsafe`), krátký, bez citlivého
  obsahu, a **sám o sobě nic neodemyká** — stránka vozidla je za
  přihlášením jako každá jiná.
- Cizí záznam = 404, ne 403, tam kde by i existence záznamu byla
  informace (např. notifikace).
- Flash hlášky se předávají jako **kód** v URL, nikdy jako text.
- Stav tachometru nikdy nesnižuje běžná operace — jen administrativní
  oprava, povinně se zdůvodněním a vždy auditovaná.
- Žádné secrets v Gitu. `.env` je mimo repozitář, `.env.example` obsahuje
  jen názvy klíčů.

## 9. Co se do aplikace nesmí dostat

Ze zadání, kapitoly 14 a 34 — jsou to záměrná rozhodnutí, ne opomenutí:

- žádný GPS tracking, historie poloh ani telematika,
- žádné sledování pohybu zaměstnanců,
- žádné napojení na DSS,
- OCR a mapová služba jsou **pomůcky**: když nefungují, aplikace musí
  fungovat dál a uživatel zadá hodnotu ručně,
- OCR nikdy nemění účetní/knižní údaje bez potvrzení uživatelem.

## 10. Rozhodnutí s dopadem na model a bezpečnost

Zaznamenávají se do `docs/ROZHODNUTI.md` (požadavek zadání, kapitola 37).
