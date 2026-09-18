# Naplánované úlohy na VPS

Kopie souborů, které na serveru leží mimo tenhle adresář. Jsou tu proto,
aby bylo z Gitu vidět, co je na serveru nastavené, a aby šlo nasazení
zopakovat — **nekopírují se tam deployem**, instalují se ručně jednou.

| soubor v repozitáři | místo na serveru |
|---|---|
| `etc-cron.d-kniha-jizd` | `/etc/cron.d/kniha-jizd` |
| `etc-logrotate.d-kniha-jizd` | `/etc/logrotate.d/kniha-jizd` |

Instalace (obojí jako root, práva 0644, vlastník `root:root`):

```bash
sudo install -m 644 -o root -g root docker/cron/etc-cron.d-kniha-jizd /etc/cron.d/kniha-jizd
sudo install -m 644 -o root -g root docker/cron/etc-logrotate.d-kniha-jizd /etc/logrotate.d/kniha-jizd
sudo mkdir -p /opt/kniha_jizd/logs
```

Jméno souboru v `/etc/cron.d/` **nesmí obsahovat tečku** — cron by ho
přeskočil bez varování.

Podrobnosti a proč zrovna 7:00 viz `docs/SERVER_SETUP.md`, kapitola 6b.
