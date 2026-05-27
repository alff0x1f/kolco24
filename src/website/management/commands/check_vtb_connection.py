import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from website.models import VTBPayment


class Command(BaseCommand):
    help = "Check VTB API connectivity and SSL certificate validity."

    def add_arguments(self, parser):
        parser.add_argument(
            "--order-id",
            default=None,
            help="VTB order_id to test get_order. Defaults to latest from DB.",
        )

    def handle(self, *args, **options):
        s = settings.VTB
        token_url = (
            "https://open.api.vtb.ru:443/passport/oauth2/token"
            if s["ENV"] == "prod"
            else "https://auth.bankingapi.ru/auth/realms/kubernetes/protocol/openid-connect/token"
        )
        api_base = (
            "https://gw.api.vtb.ru/openapi/smb/efcp/e-commerce/v1"
            if s["ENV"] == "prod"
            else "https://hackaton.bankingapi.ru/api/smb/efcp/e-commerce/api/v1"
        )

        order_id = options["order_id"]
        if not order_id:
            last = VTBPayment.objects.order_by("-id").first()
            if not last:
                self.stderr.write("No VTBPayment records in DB, cannot test get_order.")
                return
            order_id = last.order_id
            self.stdout.write(f"Using last order_id from DB: {order_id}")

        for verify in (True, False):
            label = "verify=True " if verify else "verify=False"
            self.stdout.write(f"\n--- {label} ---")

            token = self._get_token(s, token_url, verify, label)
            if token is None:
                continue

            self._get_order(s, api_base, order_id, token, verify, label)

    def _get_token(self, s, token_url, verify, label):
        data = {
            "grant_type": "client_credentials",
            "client_id": s["CLIENT_ID"],
            "client_secret": s["CLIENT_SECRET"],
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        t0 = time.monotonic()
        try:
            resp = requests.post(
                token_url, data=data, headers=headers, timeout=15, verify=verify
            )
            elapsed = time.monotonic() - t0
            resp.raise_for_status()
            token = resp.json()["access_token"]
            self.stdout.write(f"  token OK  HTTP {resp.status_code}  {elapsed:.2f}s")
            return token
        except requests.exceptions.SSLError as e:
            elapsed = time.monotonic() - t0
            self.stderr.write(f"  token FAIL (SSL)  {elapsed:.2f}s  {e}")
        except Exception as e:
            elapsed = time.monotonic() - t0
            self.stderr.write(f"  token FAIL  {elapsed:.2f}s  {e}")
        return None

    def _get_order(self, s, api_base, order_id, token, verify, label):
        url = f"{api_base}/orders/{order_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "X-IBM-Client-Id": s["CLIENT_ID_HEADER"],
            "Content-Type": "application/json",
        }
        if s.get("MERCHANT_AUTH"):
            headers["Merchant-Authorization"] = s["MERCHANT_AUTH"]
        t0 = time.monotonic()
        try:
            resp = requests.get(url, headers=headers, timeout=15, verify=verify)
            elapsed = time.monotonic() - t0
            # 404 is fine — SSL and auth work, order just may not exist
            self.stdout.write(
                f"  get_order OK  HTTP {resp.status_code}  {elapsed:.2f}s"
            )
        except requests.exceptions.SSLError as e:
            elapsed = time.monotonic() - t0
            self.stderr.write(f"  get_order FAIL (SSL)  {elapsed:.2f}s  {e}")
        except Exception as e:
            elapsed = time.monotonic() - t0
            self.stderr.write(f"  get_order FAIL  {elapsed:.2f}s  {e}")
