import hashlib
import hmac
import os

import httpx
from fastapi import HTTPException

BASE_URL = "https://api.razorpay.com/v1"


def credentials() -> tuple[str, str]:
    key = os.getenv("RAZORPAY_KEY_ID", "")
    secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    if not key.startswith("rzp_test_") or not secret:
        raise HTTPException(503, "Razorpay test keys are not configured")
    return key, secret


def create_order(amount_paise: int, receipt: str) -> dict:
    key, secret = credentials()
    try:
        response = httpx.post(f"{BASE_URL}/orders", auth=(key, secret), json={"amount": amount_paise, "currency": "INR", "receipt": receipt}, timeout=10)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Payment service is unavailable") from None


def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    _, secret = credentials()
    expected = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


def fetch_payment(payment_id: str) -> dict:
    key, secret = credentials()
    try:
        response = httpx.get(f"{BASE_URL}/payments/{payment_id}", auth=(key, secret), timeout=10)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Could not verify payment status") from None


def fetch_order_payments(order_id: str) -> list[dict]:
    try:
        response = httpx.get(f"{BASE_URL}/orders/{order_id}/payments", auth=credentials(), timeout=10)
        response.raise_for_status()
        items = response.json()["items"]
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("Invalid payment list")
        return items
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(502, "Could not reconcile payment status") from None


def verify_webhook(body: bytes, signature: str) -> bool:
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "Payment webhook is unavailable")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


def create_refund(payment_id: str, amount_paise: int, receipt: str) -> dict:
    response = httpx.post(
        f"{BASE_URL}/payments/{payment_id}/refund", auth=credentials(), timeout=10,
        headers={"X-Refund-Idempotency": receipt},
        json={"amount": amount_paise, "speed": "normal", "receipt": receipt},
    )
    response.raise_for_status()
    return response.json()


def fetch_refund(payment_id: str, refund_id: str) -> dict:
    response = httpx.get(f"{BASE_URL}/payments/{payment_id}/refunds/{refund_id}", auth=credentials(), timeout=10)
    response.raise_for_status()
    return response.json()


def find_refund(payment_id: str, receipt: str) -> dict | None:
    response = httpx.get(f"{BASE_URL}/payments/{payment_id}/refunds", auth=credentials(),
                         params={"count": 100}, timeout=10)
    response.raise_for_status()
    return next((item for item in response.json()["items"] if item.get("receipt") == receipt), None)
