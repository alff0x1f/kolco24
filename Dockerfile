FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

COPY ru-trust-bundle.pem /usr/local/share/ca-certificates/ru-trust-bundle.crt
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates postgresql-client-15 \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./

RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "kolco24.wsgi:application"]
