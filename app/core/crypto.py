"""Šifrování citlivých hodnot uložených v databázi.

Vzniklo kvůli jediné věci: heslo k SMTP se dá nastavit v administraci, a
tím pádem leží v databázi. Databáze se před každým deployem zálohuje do
`/opt/kniha_jizd/backups/postgres/*.sql` a takový dump je obyčejný text —
heslo k firemní poště by v něm bylo čitelné, v tuctu kopií, navždycky.

Klíč se odvozuje ze `SESSION_SECRET_KEY`, který je povinný, leží v `.env`
mimo Git a do zálohy databáze se nikdy nedostane. Záloha tedy obsahuje
jen šifrový text, se kterým se bez přístupu k serveru nedá nic dělat.

**Není to ochrana proti tomu, kdo je na serveru root.** Kdo přečte `.env`,
přečte i heslo — a to je v pořádku, tohle chrání únik zálohy, ne server.

Rotace `SESSION_SECRET_KEY` znamená, že se uložené heslo přestane dát
rozšifrovat. `decrypt_secret` v tom případě vrátí `None` a aplikace se
zachová, jako by heslo nastavené nebylo: požádá administrátora, ať ho
zadá znovu. Nikdy kvůli tomu nespadne.
"""
import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

# Aby bylo na první pohled poznat, že hodnota je šifrovaná, a nešlo si ji
# splést s heslem zadaným omylem natvrdo.
PREFIX = "enc:v1:"


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    # Fernet chce 32 bajtů v urlsafe base64; SESSION_SECRET_KEY je
    # libovolně dlouhý řetězec, takže se protáhne SHA-256.
    digest = hashlib.sha256(get_settings().session_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    """Zašifruje hodnotu k uložení. Prázdný vstup zůstane prázdný - to
    není tajemství, to je „nenastaveno"."""
    if not plain:
        return ""
    return PREFIX + _cipher().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(stored: str | None) -> str | None:
    """Vrátí původní hodnotu, nebo `None`, když ji nelze získat.

    `None` znamená „tuhle hodnotu nemáme" - ať už proto, že nic uloženo
    není, nebo že se změnil klíč. Volající to má řešit jako nenastavené,
    ne jako chybu."""
    if not stored:
        return None
    if not stored.startswith(PREFIX):
        # Hodnota zapsaná ručně do databáze mimo aplikaci. Bere se tak,
        # jak je - jinak by administrátor nemohl nic opravit zvenčí.
        return stored
    try:
        return _cipher().decrypt(stored[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def is_encrypted(stored: str | None) -> bool:
    return bool(stored) and stored.startswith(PREFIX)
