"""Testovací prostředí.

Testy běží proti VLASTNÍ, jednorázové databázi `kniha_jizd_test` na
lokálním dev PostgreSQL kontejneru (docker/docker-compose.dev.yml), který
poslouchá na 127.0.0.1:5435. Nikdy proti dev databázi, nikdy proti VPS a
nikdy proti databázi jiného projektu na stejném stroji.

Schéma se do ní nalévá skutečnou alembic migrací, ne
`Base.metadata.create_all` - takže testy zároveň ověřují, že migrace
opravdu popisuje to, co modely čekají.
"""
import asyncio
import os
import re
import uuid

os.environ["KJ_DB_NAME"] = "kniha_jizd_test"
os.environ["KJ_DB_USER"] = "kniha_jizd_dev"
os.environ["KJ_DB_PASSWORD"] = "dev_only_password"
os.environ["DB_HOST"] = "127.0.0.1"
os.environ["DB_PORT"] = "5435"
os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret")
# Ne "production" - jinak by session cookie byla Secure-only a přes http
# testovací transport by se nepřenesla.
os.environ["ENVIRONMENT"] = "development"
os.environ["PHOTOS_DIR"] = "_local_data/test_photos"
os.environ["DOCUMENTS_DIR"] = "_local_data/test_documents"
os.environ["SMTP_HOST"] = ""  # žádné odesílání e-mailů z testů

import asyncpg  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

ADMIN_DSN = dict(
    user="kniha_jizd_dev", password="dev_only_password", host="127.0.0.1", port=5435, database="kniha_jizd_dev",
)
TEST_PASSWORD = "TestPassword123!"


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    async def _recreate():
        conn = await asyncpg.connect(**ADMIN_DSN)
        await conn.execute("DROP DATABASE IF EXISTS kniha_jizd_test")
        await conn.execute("CREATE DATABASE kniha_jizd_test")
        await conn.close()

    asyncio.run(_recreate())
    command.upgrade(Config("alembic.ini"), "head")

    yield

    async def _drop():
        from app.core.db import engine

        await engine.dispose()
        conn = await asyncpg.connect(**ADMIN_DSN)
        await conn.execute("DROP DATABASE IF EXISTS kniha_jizd_test")
        await conn.close()

    asyncio.run(_drop())


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_after_test():
    yield
    from app.core.db import engine

    await engine.dispose()


async def _make_user(role_name: str, label: str) -> tuple[str, str, str]:
    from app.core.db import async_session_factory
    from app.core.security import hash_password
    from app.models.core import Role, User, UserRole

    email = f"{label}-{uuid.uuid4().hex[:8]}@test.local"
    async with async_session_factory() as db:
        role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one()
        user = User(email=email, full_name=f"Test {label}", password_hash=hash_password(TEST_PASSWORD))
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.commit()
        return email, TEST_PASSWORD, str(user.id)


@pytest_asyncio.fixture
async def client():
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def anon_client():
    """Druhý, nikdy nepřihlášený klient. Potřeba všude, kde test používá
    zároveň `logged_in_client` - ten totiž staví na `client`, takže
    požádat o oba v jednom testu by vrátilo tutéž (už přihlášenou)
    instanci."""
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_user():
    return await _make_user("admin", "admin")


@pytest_asyncio.fixture
async def basic_user():
    """Řidič - vidí vozidla, půjčuje si je, ale nespravuje je."""
    return await _make_user("user", "driver")


@pytest_asyncio.fixture
async def responsible_user():
    """Odpovědná osoba. Roli má, ale rozsah „jen moje vozidla" vzniká až
    tím, že ji test přiřadí konkrétnímu vozidlu jako responsible_user_id."""
    return await _make_user("odpovedna_osoba", "responsible")


async def login(ac: AsyncClient, credentials: tuple[str, str, str]) -> AsyncClient:
    email, password, _ = credentials
    response = await ac.post("/kniha-jizd/login", data={"email": email, "password": password}, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert "kniha_jizd_session" in response.cookies
    return ac


@pytest_asyncio.fixture
async def logged_in_client(client, admin_user):
    return await login(client, admin_user)


def extract_csrf_token(html: str) -> str:
    """Token se čte z vykreslené stránky stejně, jako by ho poslal
    prohlížeč - ne sáhnutím dovnitř session."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "na stránce není CSRF token"
    return match.group(1)


@pytest_asyncio.fixture
async def csrf_token(logged_in_client):
    """CSRF token je vázaný na session, ne na konkrétní formulář (viz
    app/core/csrf.py), takže jedno načtení stačí pro všechny POSTy
    v témže testu."""
    response = await logged_in_client.get("/kniha-jizd/vehicles/new")
    return extract_csrf_token(response.text)


VEHICLE_FORM = {
    "internal_code": "VOZ-01",
    "license_plate": "1AB 2345",
    "brand": "Škoda",
    "model": "Octavia",
    "vehicle_type": "osobni",
    "fuel_type": "nafta",
    "status": "available",
    "is_active": "1",
    "current_odometer_km": "100000",
    "current_fuel_level": "50",
}


async def create_vehicle(ac: AsyncClient, csrf: str, **overrides) -> str:
    """Založí vozidlo přes web formulář a vrátí jeho id z redirectu."""
    data = {**VEHICLE_FORM, "csrf_token": csrf, **overrides}
    response = await ac.post("/kniha-jizd/vehicles/new", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/vehicles/")[1].split("?")[0]


MULTIPART_BOUNDARY = "----WebKitFormBoundaryTEST"


def browser_multipart(fields: dict[str, str], *, empty_file_field: str) -> bytes:
    """Přesně to, co pošle prohlížeč u formuláře s nevyplněným souborovým
    polem: part se NEVYNECHÁ, pošle se s prázdným filename.

    Testovací klienti to dělají jinak, takže tenhle tvar jde sestavit jen
    ručně - a právě on je v terénu nejčastější (řidič fotku nepořídí)."""
    parts = [
        f"--{MULTIPART_BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in fields.items()
    ]
    parts.append(
        f"--{MULTIPART_BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="{empty_file_field}"; filename=""\r\n'
        "Content-Type: application/octet-stream\r\n\r\n\r\n"
    )
    parts.append(f"--{MULTIPART_BOUNDARY}--\r\n")
    return "".join(parts).encode("utf-8")
