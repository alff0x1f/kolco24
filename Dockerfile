FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

COPY ru-trust-bundle.pem /usr/local/share/ca-certificates/ru-trust-bundle.crt
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates postgresql-client \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./

RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "config.wsgi:application"]
