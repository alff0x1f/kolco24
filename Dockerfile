# Use an official Python runtime as a parent image
FROM python:3.9

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Force Python/requests to use the system CA bundle (will include our RU bundle)
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Set working directory in the container
WORKDIR /app

COPY ru-trust-bundle.pem /usr/local/share/ca-certificates/ru-trust-bundle.crt

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates; \
    update-ca-certificates; \
    rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the website directory contents into the container at /app
COPY src ./

# Collect static files
RUN python manage.py collectstatic --noinput

# Run the application
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "kolco24.wsgi:application"]
