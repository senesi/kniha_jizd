# --- builder ---------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir --prefix=/install .

# --- runtime ---------------------------------------------------------
FROM python:3.12-slim

RUN groupadd --system --gid 1000 knihajizd && \
    useradd --system --uid 1000 --gid knihajizd --no-create-home --shell /usr/sbin/nologin knihajizd

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

RUN chown -R knihajizd:knihajizd /app

USER knihajizd

EXPOSE 8000

# --forwarded-allow-ips=* - důvěřovat hlavičkám X-Forwarded-Proto/-For od
# toho, kdo se připojí na port 8000 tohoto kontejneru. Bezpečné právě
# proto, že tenhle port není nikdy vystavený ven: compose ho na VPS
# publikuje jen jako "127.0.0.1:<port>:8000", takže druhou stranou může
# být výhradně lokální nginx, nikdy skutečný klient.
#
# Bez toho uvicorn hlavičku ignoruje (výchozí forwarded-allow-ips je
# "127.0.0.1", což neodpovídá IP docker bridge gateway, ze které
# proxovaný požadavek doopravdy přichází) a request.base_url hlásí "http"
# i u skutečného HTTPS požadavku - což by rozbilo schéma v URL
# generovaných do QR kódů (viz vehicles/router_web.py:_qr_url).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
