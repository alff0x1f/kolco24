import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from django.conf import settings


@dataclass
class VTBConfig:
    env: str
    client_id: str
    client_secret: str
    client_id_header: str
    merchant_auth: Optional[str]
    return_url_base: str

    @property
    def token_url(self) -> str:
        if self.env == "prod":
            return "https://open.api.vtb.ru:443/passport/oauth2/token"
        return (
            "https://auth.bankingapi.ru/"
            "auth/realms/kubernetes/protocol/openid-connect/token"
        )

    @property
    def api_base(self) -> str:
        if self.env == "prod":
            return "https://gw.api.vtb.ru/openapi/smb/efcp/e-commerce/v1"
        return "https://hackaton.bankingapi.ru/api/smb/efcp/e-commerce/api/v1"


class VTBClient:
    _token: Optional[str] = None
    _token_exp: float = 0.0

    def __init__(self, cfg: Optional[VTBConfig] = None):
        if cfg is None:
            s = settings.VTB
            cfg = VTBConfig(
                env=s["ENV"],
                client_id=s["CLIENT_ID"],
                client_secret=s["CLIENT_SECRET"],
                client_id_header=s["CLIENT_ID_HEADER"],
                merchant_auth=s["MERCHANT_AUTH"],
                return_url_base=s["RETURN_URL_BASE"],
            )
        self.cfg = cfg
        self.session = requests.Session()

    def _ensure_token(self):
        now = time.time()
        if self._token and now < self._token_exp - 10:
            return
        # OAuth2 client_credentials — x-www-form-urlencoded
        data = {
            "grant_type": "client_credentials",
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = self.session.post(
            self.cfg.token_url, data=data, headers=headers, timeout=15
        )
        resp.raise_for_status()
        j = resp.json()
        self._token = j["access_token"]
        # в проде ~170s, в песочнице ~300s — возьмём то, что вернулось
        self._token_exp = now + int(j.get("expires_in", 150))

    def _headers(self) -> Dict[str, str]:
        self._ensure_token()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-IBM-Client-Id": self.cfg.client_id_header,  # нижний регистр, без домена
            "Content-Type": "application/json",
        }
        if self.cfg.merchant_auth:
            headers["Merchant-Authorization"] = self.cfg.merchant_auth
        return headers

    def create_order(
        self,
        *,
        order_id: str,
        order_name: str,
        amount_value: float,
        currency_code: str = "RUB",
        return_url: Optional[str] = None,
        customer: Optional[Dict[str, Any]] = None,
        bundle: Optional[Dict[str, Any]] = None,
        return_payment_data: Optional[str] = None,
        # "sbp" — чтобы вернуть ссылку/QR для СБП
        additionalinfo: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Создать ордер (одностадийный платёж по карте / СБП)"""
        payload: Dict[str, Any] = {
            "orderId": order_id,
            "orderName": order_name,
            "amount": {"value": amount_value, "code": currency_code},
        }
        if return_url:
            payload["returnUrl"] = return_url
        if customer:
            payload["customer"] = customer
        if bundle:
            payload["bundle"] = bundle  # для 54-ФЗ товарной корзины
        if return_payment_data:
            payload["returnPaymentData"] = return_payment_data
        if additionalinfo:
            payload["additionalinfo"] = additionalinfo

        url = f"{self.cfg.api_base}/orders"
        r = self.session.post(url, headers=self._headers(), json=payload, timeout=20)
        r.raise_for_status()
        return r.json()

    def get_order(self, order_id: str) -> Dict[str, Any]:
        url = f"{self.cfg.api_base}/orders/{order_id}"
        r = self.session.get(url, headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def refund(
        self,
        *,
        refund_id: str,
        payment_id: str,
        amount_value: float,
        currency_code: str = "RUB",
        bundle: Optional[
            Dict[str, Any]
        ] = None,  # для частичного возврата с корзиной (54-ФЗ)
        additionalinfo: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "refundId": refund_id,
            "paymentId": payment_id,
            "amount": {"value": amount_value, "code": currency_code},
        }
        if bundle:
            payload["bundle"] = bundle
        if additionalinfo:
            payload["additionalinfo"] = additionalinfo

        url = f"{self.cfg.api_base}/refunds"
        r = self.session.post(url, headers=self._headers(), json=payload, timeout=20)
        r.raise_for_status()
        return r.json()
