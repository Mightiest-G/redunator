"""Redunator backend — Google Play subscription verification.

POST /subscriptions/verify (requires a device JWT, see backend/auth.py)

Request body: {"product_id", "purchase_token", "device_id"}.
``device_id`` must match the device in the Bearer token — a token
for one device cannot verify another device's purchase.

Verification goes to the Google Play Developer API
(androidpublisher purchases.subscriptionsv2.get) using a service
account whose JSON file is pointed at by the
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON env var (a filesystem path, not
the JSON itself — never put credentials in an env var value).

When the service account is not configured the endpoint still
answers 200 with {"valid": null, "reason":
"play_verification_not_configured"} — the same fallback pattern as
GET /app/latest — so the app simply treats Pro status as unknown
and keeps working.

STATELESS (deliberate, for now): no entitlement is stored
server-side. The app caches Pro status locally and re-validates
on launch. A later phase can persist verified entitlements in the
user store (keyed by device_id) so a device stays Pro across
reinstalls without re-purchasing; that needs a migration of the
user_settings table, not a change to this endpoint's contract.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ..auth import require_device

router = APIRouter()

PACKAGE_NAME = "com.redunator.redunator"  # app's applicationId
PLAY_SCOPE = "https://www.googleapis.com/auth/androidpublisher"

# Subscription states in which Google still grants entitlement.
ENTITLED_STATES = {
    "SUBSCRIPTION_STATE_ACTIVE",
    "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
}


def _err(status: int, error: str, message: str):
    return JSONResponse(status_code=status,
                        content={"error": error, "message": message})


class PlayVerificationError(RuntimeError):
    """Google could not be reached, or the service account is wrong —
    i.e. NOT a verdict about the purchase itself."""


def _verify_with_google(product_id: str, purchase_token: str) -> dict:
    """Call purchases.subscriptionsv2.get and reduce it to the
    endpoint's shape.

    Returns {"valid": bool, "expiry": str|None, "auto_renewing":
    bool|None, "reason": str|None}. A token Google does not know
    (400/404 from the API) is a verdict — valid False — not an
    error. Anything else from Google (auth failure, 5xx, network)
    raises PlayVerificationError. Google client libraries are
    imported lazily so the backend still starts (and the
    not-configured path still works) without them installed.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    sa_path = os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "")
    try:
        creds = service_account.Credentials.from_service_account_file(
            sa_path, scopes=[PLAY_SCOPE])
        service = build("androidpublisher", "v3", credentials=creds,
                        cache_discovery=False)
        resp = (service.purchases().subscriptionsv2().get(
            packageName=PACKAGE_NAME, productId=product_id,
            token=purchase_token).execute())
    except HttpError as e:
        status = getattr(e.resp, "status", None)
        if status in (400, 404):
            # Google does not know this purchase token: the purchase
            # is not valid. (Do not leak Google's error text.)
            return {"valid": False, "expiry": None, "auto_renewing": None,
                    "reason": "purchase_token_invalid"}
        raise PlayVerificationError(
            f"Google Play API returned HTTP {status}")
    except Exception as e:
        # FileNotFoundError (bad path), auth failure, network error:
        # the verification itself could not run. Never leak the
        # service-account path or Google's message to the client.
        raise PlayVerificationError(
            "Google Play verification could not run") from e

    state = resp.get("subscriptionState")
    line_items = resp.get("lineItems") or []
    line = next((li for li in line_items
                 if li.get("productId") == product_id),
                line_items[0] if line_items else None)
    expiry = line.get("expiryTime") if line else None
    auto_plan = line.get("autoRenewingPlan") if line else None
    auto_renewing = (bool(auto_plan.get("autoRenewEnabled", True))
                     if auto_plan else None)
    if auto_renewing is None and line is not None:
        auto_renewing = False
    return {
        "valid": state in ENTITLED_STATES,
        "expiry": expiry,
        "auto_renewing": auto_renewing,
        "reason": None,
    }


@router.post("/subscriptions/verify")
def verify_subscription(
        payload: dict = Body(...),
        device_id: str = Depends(require_device)):
    product_id = payload.get("product_id")
    purchase_token = payload.get("purchase_token")
    body_device_id = payload.get("device_id")
    if not product_id or not purchase_token or not body_device_id:
        return _err(400, "invalid_parameters",
                    "product_id, purchase_token and device_id are all "
                    "required.")
    if str(body_device_id) != device_id:
        return _err(401, "unauthorized",
                    "device_id in the body does not match the device in "
                    "the Bearer token.")
    if not os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", ""):
        return {"valid": None, "reason": "play_verification_not_configured"}
    try:
        result = _verify_with_google(str(product_id), str(purchase_token))
    except PlayVerificationError as e:
        return _err(502, "play_verification_failed", str(e))
    return {
        "valid": result["valid"],
        "product_id": str(product_id),
        "expiry": result["expiry"],
        "auto_renewing": result["auto_renewing"],
        "reason": result["reason"],
    }
