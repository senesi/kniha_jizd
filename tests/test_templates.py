"""Šablony se musí aspoň přeložit (zadání 31).

Tenhle soubor vznikl kvůli konkrétní chybě: `user_form.html` měla od
Etapy 1 syntaktickou chybu v Jinja výrazu, takže obrazovka „upravit
uživatele" padala na 500. Nikdo si toho nevšiml, protože testy tu
stránku neotevíraly - a chyba v šabloně se projeví až při vykreslení,
ne při startu aplikace.

Překlad všech šablon je levná pojistka, která tuhle třídu chyb chytí
celou najednou, i u obrazovek, které zatím nemá kdo testovat.
"""
import pathlib

import pytest
from jinja2 import TemplateSyntaxError

from app.core.templates import templates

TEMPLATES_DIR = pathlib.Path("app/templates")
ALL_TEMPLATES = sorted(
    path.relative_to(TEMPLATES_DIR).as_posix() for path in TEMPLATES_DIR.rglob("*.html")
)


def test_there_are_templates_to_check():
    """Kdyby se cesta rozešla se skutečností, test níž by tiše neověřoval
    vůbec nic."""
    assert len(ALL_TEMPLATES) > 30


@pytest.mark.parametrize("name", ALL_TEMPLATES)
def test_template_compiles(name):
    try:
        templates.env.get_template(name)
    except TemplateSyntaxError as error:
        pytest.fail(f"{name}:{error.lineno}: {error.message}")


# --- obrazovky, které chyběly v pokrytí --------------------------------

async def test_user_form_pages_render(logged_in_client, admin_user):
    """Přesně ty dvě stránky, které tou chybou padaly."""
    new_page = await logged_in_client.get("/kniha-jizd/users/new")
    assert new_page.status_code == 200
    assert "Založit uživatele" in new_page.text

    admin_id = admin_user[2]
    edit_page = await logged_in_client.get(f"/kniha-jizd/users/{admin_id}/edit")
    assert edit_page.status_code == 200
    assert "Uložit změny" in edit_page.text
    # Aktivní účet má zaškrtnuto.
    assert 'name="is_active"' in edit_page.text
    assert "checked" in edit_page.text


async def test_user_edit_keeps_the_submitted_state_after_an_error(
    logged_in_client, csrf_token, admin_user, basic_user,
):
    """Po chybě se zaškrtnutí bere z odeslaného formuláře, ne z databáze."""
    from app.core.db import async_session_factory
    from sqlalchemy import select
    from app.models.core import User

    async with async_session_factory() as db:
        target = (await db.execute(select(User).where(User.email == basic_user[0]))).scalar_one()
    assert target.is_active is True

    # Kolize e-mailu: uložení selže a formulář se překreslí. Účet přitom
    # zůstává aktivní v databázi, ale odškrtnutý ve formuláři.
    response = await logged_in_client.post(
        f"/kniha-jizd/users/{target.id}/edit",
        data={"csrf_token": csrf_token, "email": admin_user[0],
              "full_name": "Pokus", "roles": "user"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    checkbox = response.text.split('name="is_active"')[1].split(">")[0]
    assert "checked" not in checkbox


# --- nahrávání souborů na mobilu ---------------------------------------

def test_no_template_forces_the_camera():
    """`capture="environment"` na iOS schová „Fotogalerie" i „Vybrat
    soubor" a nechá jen „Vyfotit teď".

    Atribut se sem dostal jako pohodlí pro řidiče u pumpy, ale zaplatilo
    se za to tím, že už vyfocenou fotku nešlo přiložit vůbec. Bez něj
    iOS nabídne obojí a fotoaparát je pořád jedno klepnutí daleko."""
    offenders = [
        name for name in ALL_TEMPLATES
        if "capture=" in (TEMPLATES_DIR / name).read_text(encoding="utf-8")
    ]
    assert offenders == [], f"šablony vynucují fotoaparát: {offenders}"


def test_file_inputs_accept_images():
    """Regrese: odstranění `capture` nesmělo shodit `accept`."""
    import re

    inputs = []
    for name in ALL_TEMPLATES:
        source = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
        for match in re.finditer(r'<input[^>]*type="file"[^>]*>', source):
            inputs.append((name, match.group(0)))

    assert inputs, "nějaká souborová pole existovat musí"
    without_accept = [(name, tag) for name, tag in inputs if "accept=" not in tag]
    assert without_accept == [], f"souborové pole bez accept: {without_accept}"
