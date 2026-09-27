"""
config.py
---------
Single place for everything the client might want to tune *before the
first run*: how many levels/slots exist, what vehicle types are
accepted, starting fee tiers per vehicle type, and the VIP surcharge.

IMPORTANT: fee tiers, the VIP surcharge, and the VAT rate below are
only used to SEED the database the very first time the app starts.
After that, the live values live in the `settings` table and are
edited from the web UI at /admin/rates (manager only) - this is what
lets management "change parking rates at any time without a software
change" (Objective 1.2). Editing the numbers below after first run has
no effect; use /admin/rates instead.
"""

import os
from dotenv import load_dotenv

# Load variables from a local .env file (if present) into the process
# environment before anything below reads them. See .env.example for
# the variables this project recognises (M-Pesa Daraja credentials).
load_dotenv()

# --- Parking layout ---------------------------------------------------
# Each level gets this many regular + vip + accessible slots.
LEVELS = {
    "L1": {"regular": 10, "vip": 2, "accessible": 2},
    "L2": {"regular": 12, "vip": 2, "accessible": 2},
}

SLOT_TYPES = ["regular", "vip", "accessible"]
VIP_SURCHARGE = 100  # Kshs, flat, added on top of the tiered fee

# --- Vehicle types & fee tiers -----------------------------------------
# Each vehicle type has its own tiered pricing, in the same
# (max_minutes, fee) band shape as the original brief, so the pricing
# logic remains a simple, auditable O(1) decision tree.
VEHICLE_TYPES = {
    "car": {
        "label": "Car",
        "tiers": [(30, 0), (120, 50), (240, 100), (360, 300), (None, 500)],
    },
    "motorcycle": {
        "label": "Motorcycle",
        "tiers": [(30, 0), (120, 20), (240, 50), (360, 150), (None, 250)],
    },
    "truck": {
        "label": "Truck / Van",
        "tiers": [(30, 0), (120, 100), (240, 200), (360, 500), (None, 900)],
    },
}

# --- Payment -----------------------------------------------------------
PAYMENT_METHODS = ["cash", "mpesa", "card"]

# VAT rate applied to every fee (Kenya standard rate, 16%). Fees are
# treated as VAT-inclusive, matching how parking rates are normally
# quoted to the public. This is only the FIRST-RUN default - once the
# app has started once, the live value lives in the database and can
# be changed by a manager at /admin/rates with no code change.
VAT_RATE = 0.16

# --- Auth ----------------------------------------------------------------
ROLES = ["attendant", "manager"]

# Seed accounts created on first run (change/remove in production).
SEED_USERS = [
    {"username": "manager", "password": "manager123", "role": "manager"},
    {"username": "attendant", "password": "attendant123", "role": "attendant"},
]

SECRET_KEY = "change-this-in-production-smartgate-v2"
DB_PATH = "parking.db"

# --- M-Pesa Daraja (optional, real integration) -------------------------
# Loaded from environment variables (via a .env file - see .env.example)
# so real credentials never sit in source control. If MPESA_CONSUMER_KEY
# and MPESA_CONSUMER_SECRET are both unset, the app automatically falls
# back to the simulated payment reference (simulate_payment() in
# algorithms.py) - the app is fully usable and gradeable with zero
# external setup; Daraja is an enhancement, not a requirement to run it.
MPESA_ENV = os.environ.get("MPESA_ENV", "sandbox")  # 'sandbox' or 'production'
MPESA_CONSUMER_KEY = os.environ.get("MPESA_CONSUMER_KEY", "")
MPESA_CONSUMER_SECRET = os.environ.get("MPESA_CONSUMER_SECRET", "")
# Safaricom's public sandbox test shortcode + passkey (documented by
# Safaricom for sandbox use, not secret) - used unless overridden.
MPESA_SHORTCODE = os.environ.get("MPESA_SHORTCODE", "174379")
MPESA_PASSKEY = os.environ.get(
    "MPESA_PASSKEY",
    "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919",
)
# Public HTTPS URL Safaricom can reach (e.g. an ngrok URL) + /mpesa/callback
MPESA_CALLBACK_URL = os.environ.get("MPESA_CALLBACK_URL", "")
