# SERVER_SETUP.md — Kniha jízd

Přístup na VPS a produkční nasazení projektu **Kniha jízd**.

Cílem je, aby se v dalších etapách nemuselo znovu zjišťovat, jak se na
server připojit a co kde běží.

> **Stav k 17. 9. 2026:** SSH ověřeno a server auditován (viz kapitola
> 2). Z tohoto projektu na VPS zatím **nic neexistuje** — sekce
> označené *(plán)* popisují, co se má vytvořit, ne co je ověřeno. Po
> prvním úspěšném deployi se přepíšou podle skutečnosti.

---

## 1. SSH přístup

| | |
|---|---|
| Alias | `netcup-fve` |
| Uživatel na VPS | `fveadmin` |
| Klíč | ED25519, cesta je v `~/.ssh/config` |
| Připojení | `ssh.exe netcup-fve` |

### Pravidla

- Používat **výhradně alias `netcup-fve`**. Nezkoušet jiné klíče, jiné
  uživatele ani přihlášení heslem.
- Privátní klíč se **nikdy** nekopíruje, nevypisuje ani neukládá do
  projektu. Do repozitáře nepatří ani jeho cesta jako secret — stačí
  alias, zbytek řeší `~/.ssh/config`.
- Po uživateli se **nikdy** nežádá heslo, passphrase ani obsah klíče.

### Na tomto stroji: PowerShell, ne Bash

SSH k VPS spouštěj přes nástroj **PowerShell**, nikdy přes Bash.

Git Bash na Windows nemá `SSH_AUTH_SOCK` napojený na službu
`ssh-agent`, takže `ssh` z Bashe skončí na `Permission denied
(publickey)`. Nativní Windows OpenSSH klient spuštěný z PowerShellu se
na agenta napojí sám (named pipe) a passphrase neřeší.

### Předpoklad: odemčený klíč v ssh-agentovi

Klíč **je chráněný passphrase**. Celý postup stojí na tom, že je
odemčený ve Windows službě `ssh-agent`. Passphrase zadává výhradně
uživatel, ručně, jednou:

```powershell
Start-Service ssh-agent
Set-Service ssh-agent -StartupType Automatic
ssh-add $env:USERPROFILE\.ssh\netcup_vps_2026_ed25519
```

`-StartupType Automatic` je důležité: ve výchozím stavu je služba
`Manual` a po restartu Windows se nespustí, takže SSH přestane fungovat
a vypadá to jako problém s klíčem.

### Diagnostika, když SSH nejde

Postupovat přesně v tomhle pořadí a **nezkoušet obejít klíčem nebo
účtem**:

```powershell
Get-Service ssh-agent          # běží? jaký StartType?
ssh-add -l                     # je klíč načtený v agentovi?
ssh.exe -v netcup-fve exit     # co přesně klient zkouší
```

Nejčastější příčina: služba `ssh-agent` je zastavená → agent nemá klíč →
`Permission denied (publickey)`. Řeší to blok výše; je to krok pro
uživatele, ne pro Claude Code.

---

## 2. Co na serveru běží (audit 17. 9. 2026)

| | |
|---|---|
| OS | Debian 13 (trixie) |
| Disk | 78 GB, využito 19 % (volných ~61 GB) |
| RAM | 3,8 GB, dostupné ~2,8 GB |
| Docker | 29.7.2, Compose v5.5.0 |

**Běžící projekty:**

| Projekt | Kontejnery | Port na hostu | Adresář |
|---|---|---|---|
| DSS | `dss-app`, `dss-postgres` | `127.0.0.1:8000` | `/opt/dss` |
| Evidence nářadí | `naradi-app`, `naradi-postgres` | `127.0.0.1:8001` | `/opt/naradi` |
| WebRTC drone | `webrtc-drone-backend-1`, `…-mediamtx-1` | `8080`, `1935`, `8888-8889`, `9997` | `/opt/webrtc-drone` |

**nginx** (`/etc/nginx/sites-enabled/`):

- `solareg.azunimb.cz` — HTTPS přes Certbot; `/naradi/` → `:8001`,
  `/` → `:8000` (DSS, který slouží jako rozcestník)
- `webrtc.azunimb.cz` — samostatný server blok

> **WordPress zatím neběží** — je plánovaný na později (potvrzeno
> 17. 9. 2026). Audit žádný WordPress kontejner ani nginx root nenašel.
> Až vznikne, doplnit do tabulky výše; deploye Knihy jízd se to netýká,
> jen je pak potřeba počítat s další routou v nginx.

**Ověřeno, že Kniha jízd s ničím nekoliduje:** adresář
`/opt/kniha_jizd` neexistuje, názvy `kniha-jizd-*` nejsou obsazené,
port `8002` je volný a cesta `/kniha-jizd/` v nginx zatím není.

### Izolace

Deploy Knihy jízd se ostatních projektů nesmí dotknout.

**Zakázáno měnit:**

- cokoliv pod `/opt/dss/`, `/opt/naradi/` a adresáři ostatních projektů,
- jejich kontejnery, image, volumes a databáze,
- jejich `.env` a konfigurační soubory,
- **existující nginx routy** — nová routa se jen přidává, stávající
  `server` bloky a `location` pravidla zůstávají beze změny,
- systémové služby (`docker`, `nginx`, `postgresql`) způsobem, který by
  ovlivnil jiný projekt — nginx se vždy jen `reload`, nikdy zbytečně
  `restart`,
- firewall, SSH konfigurace, uživatelé.

**Před jakoukoliv změnou nginx** nejdřív vypsat současný stav
(`nginx -T`), teprve pak přidávat.

Když není jisté, jestli je zdroj sdílený, nebo patří jinému projektu —
**nejdřív ověřit**, pak měnit.

---

## 3. Struktura na VPS *(plán)*

```text
/opt/kniha_jizd/
├── app/                 # git checkout (origin/main)
├── docker/              # docker-compose.yml pro produkci
├── config/
│   └── .env             # secrets, MIMO Git, práva 600
├── data/
│   ├── postgres/        # databáze (bind mount)
│   ├── photos/          # tachometr, účtenky, závady, vozidla
│   └── documents/       # TP, OTP, zelená karta…
└── backups/
    └── postgres/        # dumpy před deployem
```

`data/` a `backups/` jsou **persistentní** — deploy kódu na ně nikdy
nesahá.

---

## 4. Docker / Compose *(plán)*

Předloha je v repozitáři: `docker/docker-compose.prod.yml`. Na VPS leží
jako `/opt/kniha_jizd/docker/docker-compose.yml`.

| Kontejner | Role | Porty |
|---|---|---|
| `kniha-jizd-app` | FastAPI (uvicorn) | `127.0.0.1:8002` → 8000 |
| `kniha-jizd-postgres` | PostgreSQL 17 | **žádné** |

- Aplikace je publikovaná **jen na loopback**. Ven vede výhradně nginx.
- Databáze nemá `ports:` vůbec — je dostupná jen uvnitř sítě stacku.
- Persistentní data jsou **bind-mounty** pod `/opt/kniha_jizd/data/`
  (`postgres/`, `photos/`, `documents/`) — stejně jako u Evidence
  nářadí. Data jsou tak vidět na disku vedle záloh a zálohují se i
  obnovují jedním způsobem.
- Aplikace běží jako neprivilegovaný uživatel (viz `docker/Dockerfile`).
- Port `8002` je **předpoklad** — před prvním deployem ověřit, že je
  volný (`ss -tulpn`), a případně zvolit jiný.

---

## 5. PostgreSQL *(plán)*

- Vlastní kontejner, vlastní volume, vlastní databáze `kniha_jizd`.
- **Nikdy** nepoužívat databázi ani volume jiného projektu.
- Aplikace se připojuje účtem `kniha_jizd_app`, nikdy `postgres`. Ten
  účet je vlastníkem jediné databáze ve **vlastním** kontejneru, který
  nemá mapovaný port na hosta a sedí na vlastní Docker síti — k datům
  jiného projektu se tedy nedostane a jiný projekt se nedostane k němu.
- Heslo a `SESSION_SECRET_KEY` se generují **na serveru** a žijí jen
  v `/opt/kniha_jizd/config/.env`. Do Gitu nepatří; v repozitáři je
  pouze `.env.example` s názvy klíčů.
- Migrace: `alembic upgrade head` přes dočasný kontejner (součást
  deploy skriptu). Je idempotentní.

---

## 6. nginx a veřejná URL *(plán)*

Veřejná adresa:

```text
https://solareg.azunimb.cz/kniha-jizd/
```

Stejný princip jako `/naradi/` u Evidence nářadí: nginx propouští cestu
**beze změny** (žádné strippování prefixu) na `127.0.0.1:8002`. Aplikace
má prefix `/kniha-jizd` napsaný napevno ve všech routách i šablonách,
takže se chová identicky lokálně i na produkci.

Nutné hlavičky: `X-Forwarded-Proto` a `X-Forwarded-For` — bez nich
aplikace generuje do QR kódů odkazy s `http` místo `https` (proto
uvicorn běží s `--proxy-headers --forwarded-allow-ips=*`, viz komentář
v `docker/Dockerfile`).

Na rozcestníku `solareg.azunimb.cz` musí přibýt položka **Kniha jízd**.

Postup: `nginx -T` → přidat `location` do existujícího `server` bloku →
`nginx -t` → `systemctl reload nginx`. Nikdy `restart`.

---

## 7. Bezpečný deploy

Skript: `scripts/deploy_vps.sh` (v repozitáři).

```powershell
ssh.exe netcup-fve
```
```bash
cd /opt/kniha_jizd/app && ./scripts/deploy_vps.sh
```

Skript se při **jakékoliv** chybě okamžitě zastaví a v tomto pořadí:

1. ověří, že checkout na VPS nemá necommitnuté změny,
2. `git fetch` + `git pull --ff-only origin main`,
3. **záloha databáze** do `backups/postgres/` — při prázdném souboru
   skončí a dál nejde,
4. `docker compose build app`,
5. `alembic upgrade head` přes dočasný kontejner,
6. restart **pouze** `kniha-jizd-app` (nikdy databáze),
7. kontrola `/healthz`.

Deploy se spouští až po tom, co lokálně prošel celý test suite a změny
jsou commitnuté a pushnuté do `main`. Push sám nasazení nespouští.

### Po deployi ověřit

```bash
docker compose ps                       # oba kontejnery běží
docker logs --tail 50 kniha-jizd-app    # bez chyb
curl -sf http://127.0.0.1:8002/healthz  # aplikace odpovídá
nginx -t                                # konfigurace validní
docker ps                               # ostatní projekty stále běží
```

A zvenčí: `https://solareg.azunimb.cz/kniha-jizd/login`.

---

## 8. Git

Repozitář: `https://github.com/senesi/kniha_jizd` (privátní, samostatný,
**bez jakékoliv vazby na DSS**).

Na server **nikdy** neukládat GitHub heslo, token ve veřejném souboru
ani privátní SSH klíč uživatele.

---

## 9. Co dělat při prvním nasazení

Pořadí, které respektuje pravidla výše:

1. Ověřit SSH (`ssh.exe netcup-fve`).
2. **Audit serveru, nic neměnit:** `df -h`, `free -h`, `docker ps -a`,
   `ss -tulpn`, `nginx -T`, `docker network ls`, `docker volume ls`.
3. Zkontrolovat kolize: adresář `/opt/kniha_jizd`, názvy kontejnerů,
   volume, port 8002, cesta `/kniha-jizd/` v nginx.
4. Vytvořit adresářovou strukturu a `config/.env` s vygenerovanými
   secrets (práva 600).
5. `git clone` do `app/`, compose do `docker/`.
6. `docker compose up -d` → migrace → první admin
   (`scripts/create_admin.py`).
7. Přidat nginx routu, `nginx -t`, `reload`.
8. Smoke test zevnitř i zvenčí.
9. **Ověřit, že DSS, Evidence nářadí i WordPress běží beze změny.**
10. Přepsat tenhle dokument podle skutečného stavu a odstranit značky
    *(plán)*.
