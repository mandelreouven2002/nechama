"""Thin Twilio REST wrapper (no SDK) for WhatsApp sending and setup."""
import base64
import hashlib
import hmac
import json
import logging

import requests
from django.conf import settings

log = logging.getLogger(__name__)
API = "https://api.twilio.com/2010-04-01"
MESSAGING = "https://messaging.twilio.com"
CONTENT = "https://content.twilio.com"


class TwilioError(Exception):
    pass


def configured():
    return bool(
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_AUTH_TOKEN
        and (settings.TWILIO_WHATSAPP_FROM or settings.TWILIO_MESSAGING_SERVICE_SID)
    )


def _auth():
    return (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def _check(resp):
    if resp.status_code >= 400:
        try:
            data = resp.json()
            detail = f"{data.get('code')} {data.get('message')}"
        except ValueError:
            detail = resp.text[:300]
        raise TwilioError(f"Twilio HTTP {resp.status_code}: {detail}")
    return resp.json() if resp.content else {}


def status_callback_url():
    return f"{settings.PUBLIC_URL}/webhooks/twilio/status/" if settings.PUBLIC_URL else None


def inbound_url():
    return f"{settings.PUBLIC_URL}/webhooks/twilio/inbound/" if settings.PUBLIC_URL else None


def send_whatsapp(phone, body=None, content_sid=None, variables=None):
    """Send a WhatsApp message; returns the Twilio message SID."""
    if not configured():
        raise TwilioError("Twilio is not configured (TWILIO_* variables)")
    data = {"To": f"whatsapp:{phone}"}
    if settings.TWILIO_MESSAGING_SERVICE_SID:
        data["MessagingServiceSid"] = settings.TWILIO_MESSAGING_SERVICE_SID
    else:
        data["From"] = f"whatsapp:{settings.TWILIO_WHATSAPP_FROM}"
    if content_sid:
        data["ContentSid"] = content_sid
        if variables:
            data["ContentVariables"] = json.dumps(variables, ensure_ascii=False)
    else:
        data["Body"] = body or ""
    callback = status_callback_url()
    if callback:
        data["StatusCallback"] = callback
    resp = requests.post(
        f"{API}/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json",
        data=data,
        auth=_auth(),
        timeout=30,
    )
    return _check(resp)["sid"]


def valid_signature(url, params, signature):
    """Validate X-Twilio-Signature (HMAC-SHA1 over URL + sorted POST params)."""
    if not settings.TWILIO_AUTH_TOKEN or not signature:
        return False
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(
        settings.TWILIO_AUTH_TOKEN.encode(), payload.encode("utf-8"), hashlib.sha1
    ).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


# --- setup helpers (used by the setup_twilio command) ------------------------

def list_whatsapp_senders():
    senders, url = [], f"{MESSAGING}/v2/Channels/Senders"
    params = {"Channel": "whatsapp", "PageSize": 50}
    while url:
        data = _check(requests.get(url, params=params, auth=_auth(), timeout=30))
        senders += data.get("senders", [])
        url = (data.get("meta") or {}).get("next_page_url")
        params = None
    return senders


def update_sender_webhook(sender_sid, callback, status_callback):
    body = {
        "webhook": {
            "callback_url": callback,
            "callback_method": "POST",
            "status_callback_url": status_callback,
            "status_callback_method": "POST",
        }
    }
    resp = requests.post(
        f"{MESSAGING}/v2/Channels/Senders/{sender_sid}", json=body, auth=_auth(), timeout=30
    )
    return _check(resp)


def update_service_inbound(service_sid, callback):
    resp = requests.post(
        f"{MESSAGING}/v1/Services/{service_sid}",
        data={
            "InboundRequestUrl": callback,
            "InboundMethod": "POST",
            "UseInboundWebhookOnNumber": "false",
        },
        auth=_auth(),
        timeout=30,
    )
    return _check(resp)


def get_content(content_sid):
    return _check(requests.get(f"{CONTENT}/v1/Content/{content_sid}", auth=_auth(), timeout=30))


def get_approval(content_sid):
    resp = requests.get(
        f"{CONTENT}/v1/Content/{content_sid}/ApprovalRequests", auth=_auth(), timeout=30
    )
    return _check(resp)


def submit_whatsapp_approval(content_sid, name, category="MARKETING"):
    resp = requests.post(
        f"{CONTENT}/v1/Content/{content_sid}/ApprovalRequests/whatsapp",
        json={"name": name, "category": category},
        auth=_auth(),
        timeout=30,
    )
    return _check(resp)
