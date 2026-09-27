"""
mpesa.py
--------
Real M-Pesa Daraja (Lipa Na M-Pesa Online / STK Push) integration.

This module is the ONLY place that talks to Safaricom's API. Two
functions matter to the rest of the app:

    is_configured()          -> True once real credentials are set
    initiate_stk_push(...)   -> sends a real STK push to a phone

If is_configured() is False (no MPESA_CONSUMER_KEY/SECRET set in the
environment / .env file), the checkout flow in app.py falls back to
the simulated payment reference in algorithms.simulate_payment() -
the whole system remains fully functional and gradeable without any
Safaricom account, ngrok tunnel, or internet access.

Daraja flow implemented here:
    1. OAuth: exchange Consumer Key + Secret for a short-lived access
       token (get_access_token()).
    2. STK Push: ask Safaricom to prompt the customer's phone for
       their M-Pesa PIN (initiate_stk_push()). Returns a
       CheckoutRequestID used to correlate the eventual callback.
    3. Callback: Safaricom calls MPESA_CALLBACK_URL (see app.py's
       /mpesa/callback route) asynchronously with the result. This
       module does not handle the callback itself - only app.py does,
       because receiving a callback is a route, not an outbound call.

Sandbox notes (see README for the full walkthrough):
    - Safaricom's sandbox does not require a real phone; it will call
      the callback URL automatically with a simulated result a few
      seconds after the STK push request, using their standard test
      MSISDN (254708374149) if that's what you send.
    - The callback URL MUST be a public HTTPS URL Safaricom's servers
      can reach - a local ngrok tunnel is the standard way to get one
      for local development.
"""

import base64
import requests
from datetime import datetime

from config import (
    MPESA_ENV, MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET,
    MPESA_SHORTCODE, MPESA_PASSKEY, MPESA_CALLBACK_URL,
)

BASE_URL = (
    "https://sandbox.safaricom.co.ke"
    if MPESA_ENV != "production"
    else "https://api.safaricom.co.ke"
)


def is_configured():
    """True once real Daraja credentials AND a public callback URL are
    set. Both are required for a real STK push to be usable end to
    end - without a reachable callback URL, Safaricom has nowhere to
    send the payment result back to."""
    return bool(MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET and MPESA_CALLBACK_URL)


def get_access_token():
    """OAuth step: exchange Consumer Key/Secret for a bearer token.
    Raises requests.HTTPError on failure - callers should catch this
    and fall back to simulated payment rather than crash the request."""
    url = f"{BASE_URL}/oauth/v1/generate?grant_type=client_credentials"
    resp = requests.get(url, auth=(MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET), timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _password_and_timestamp():
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    raw = f"{MPESA_SHORTCODE}{MPESA_PASSKEY}{timestamp}"
    password = base64.b64encode(raw.encode()).decode()
    return password, timestamp


def initiate_stk_push(phone_number: str, amount: float, account_reference: str, transaction_desc: str):
    """
    Sends a real STK push (Lipa Na M-Pesa Online) request. phone_number
    must be in 2547XXXXXXXX format (no '+', no leading 0). Amount is
    rounded to the nearest whole shilling - M-Pesa does not do cents.

    Returns the parsed JSON response on success, which includes
    CheckoutRequestID (store this - the callback identifies the
    transaction by it). Raises requests.HTTPError / KeyError on
    failure; callers must handle this and fall back gracefully.
    """
    token = get_access_token()
    password, timestamp = _password_and_timestamp()

    payload = {
        "BusinessShortCode": MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": max(1, round(amount)),
        "PartyA": phone_number,
        "PartyB": MPESA_SHORTCODE,
        "PhoneNumber": phone_number,
        "CallBackURL": MPESA_CALLBACK_URL,
        "AccountReference": account_reference[:12],  # Daraja limits this field's length
        "TransactionDesc": transaction_desc[:13],
    }
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(
        f"{BASE_URL}/mpesa/stkpush/v1/processrequest",
        json=payload, headers=headers, timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def normalise_phone(raw: str):
    """Accepts 07XXXXXXXX, 7XXXXXXXX, or 2547XXXXXXXX and returns the
    2547XXXXXXXX format Daraja requires, or None if it doesn't look
    like a Kenyan mobile number."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("254") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "254" + digits[1:]
    if len(digits) == 9:
        return "254" + digits
    return None
