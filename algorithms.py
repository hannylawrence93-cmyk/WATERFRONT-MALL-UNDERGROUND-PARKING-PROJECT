"""
algorithms.py
-------------
The "brain" of the system, same design philosophy as v1 but extended:

Modules:
    1. Slot Management       -> SlotManager class
    2. Vehicle Entry          -> SlotManager.park_vehicle()
    3. Fee Calculation        -> calculate_fee()  (per vehicle type + VIP surcharge)
    4. Vehicle Exit / Payment -> SlotManager.checkout_vehicle()
    5. Payment simulation     -> simulate_payment()
    6. Barrier Control        -> simulated by the receipt's "barrier" field
    7. Rate Management        -> RateManager class (live-editable pricing)
    8. Exception Handling     -> SlotManager.find_active_plate_by_slot()
                                 (lost-ticket lookup by bay instead of plate)

Data structures, and why:
    - Array/list (self.slots): ordered, indexable list mirroring the
      physical layout -> O(1) access for rendering the grid.

    - One queue (deque) PER (level, slot_type): free slot numbers are
      held per level *and* per type, so a VIP request never accidentally
      consumes a regular bay and vice versa. Allocate = popleft(),
      release = append(), both O(1). FIFO keeps wear spread evenly.

    - Hash map (self.active_sessions): plate_number -> session dict,
      O(1) lookup on exit instead of an O(n) scan, same as v1 but now
      the lookup also carries vehicle_type/slot info for fee calc.

    - Hash map (self.slot_by_id / self.slot_by_number): O(1) slot
      lookup instead of scanning self.slots repeatedly (v1 used a
      linear `next(...)` scan; this removes that O(n) hot path).

    - Hash map (RateManager.rates): vehicle_type -> tier list, loaded
      from the `settings` table into memory once and refreshed on every
      save, so fee calculation stays O(1) even though rates are now
      user-editable data instead of hard-coded constants.
"""

import json
import random
import string
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from database import get_conn
from config import VEHICLE_TYPES


@dataclass
class Slot:
    id: int
    slot_number: str
    level: str
    slot_type: str
    status: str  # 'available' | 'occupied' | 'out_of_service'
    plate_number: str = None


# ---------------------------------------------------------------------------
# Module 7: Rate Management (Objective 1.2 - "change rates at any time
# without a software change")
# ---------------------------------------------------------------------------
class RateManager:
    """
    Holds fee tiers, VIP surcharge and VAT rate as DATA in the database
    (`settings` table), not as constants in code. A manager edits them
    from /admin/rates; calculate_fee() always reads the current
    in-memory copy, which is refreshed the instant a save happens - no
    code change, no redeploy, no restart.
    """

    def __init__(self):
        self.vehicle_types = {}   # vehicle_type -> {"label":..., "tiers": [...]}
        self.vip_surcharge = 0
        self.vat_rate = 0.0
        self.reload()

    def reload(self):
        try:
            with get_conn() as conn:
                rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
        except Exception:
            # settings table doesn't exist yet (e.g. algorithms.py imported
            # before database.init_db() has run) - fall back to config.py
            # defaults so the app doesn't crash; a later reload() picks up
            # the real DB-backed values once init_db() has run.
            rows = {}
        self.vehicle_types = json.loads(rows.get("vehicle_types", json.dumps(VEHICLE_TYPES)))
        self.vip_surcharge = json.loads(rows.get("vip_surcharge", "0"))
        self.vat_rate = json.loads(rows.get("vat_rate", "0.16"))

    def get_tiers(self, vehicle_type: str):
        return self.vehicle_types.get(vehicle_type, next(iter(self.vehicle_types.values())))["tiers"]

    def save(self, vehicle_types: dict, vip_surcharge: float, vat_rate: float):
        """Persist new rates and refresh the in-memory copy immediately."""
        with get_conn() as conn:
            conn.execute("UPDATE settings SET value=? WHERE key='vehicle_types'", (json.dumps(vehicle_types),))
            conn.execute("UPDATE settings SET value=? WHERE key='vip_surcharge'", (json.dumps(vip_surcharge),))
            conn.execute("UPDATE settings SET value=? WHERE key='vat_rate'", (json.dumps(vat_rate),))
        self.reload()


rate_manager = RateManager()


# ---------------------------------------------------------------------------
# Module 3: Fee Calculation Algorithm
# ---------------------------------------------------------------------------
def calculate_fee(entry_time: datetime, exit_time: datetime, vehicle_type: str, slot_type: str):
    """
    Tiered fee algorithm, parameterised by vehicle_type (each type has
    its own band table, live from RateManager) plus a flat VIP
    surcharge if the vehicle used a VIP slot. Still O(1): a fixed
    number of bands, plus one comparison, regardless of dataset size.
    Rates can change between two calls to this function (a manager may
    have just edited them) with zero code change - that's the point.

    Returns (fee, duration_minutes, vat_amount). Fees are treated as
    VAT-inclusive (how parking rates are normally advertised), so
    vat_amount is backed out of the fee for reconciliation, not added
    on top: vat_amount = fee * rate / (1 + rate).
    """
    duration_minutes = (exit_time - entry_time).total_seconds() / 60
    tiers = rate_manager.get_tiers(vehicle_type)

    fee = tiers[-1][1]  # fallback to the highest band
    for max_minutes, band_fee in tiers:
        if max_minutes is None or duration_minutes <= max_minutes:
            fee = band_fee
            break

    if slot_type == "vip" and fee > 0:
        fee += rate_manager.vip_surcharge

    vat_rate = rate_manager.vat_rate
    vat_amount = round(fee * vat_rate / (1 + vat_rate), 2) if fee > 0 else 0.0

    return fee, duration_minutes, vat_amount


def simulate_payment(method: str, amount: float):
    """
    Simulated payment gateway. Real integration points are marked
    clearly so a client can swap this for a live M-Pesa STK push or
    card processor without touching the rest of the app.
    """
    if method == "mpesa":
        ref = "MPESA" + "".join(random.choices(string.digits, k=8))
    elif method == "card":
        ref = "CARD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    else:
        ref = "CASH-" + datetime.now().strftime("%H%M%S")
    return ref


# ---------------------------------------------------------------------------
# Modules 1, 2, 4: Slot Management / Entry / Exit
# ---------------------------------------------------------------------------
class SlotManager:
    """Fast in-memory view of the car park, backed by SQLite so the two
    never drift apart and nothing is lost on restart."""

    def __init__(self):
        self.slots = []                 # ARRAY: ordered list of Slot objects
        self.queues = {}                # QUEUE per (level, slot_type) -> deque of slot_number
        self.slot_by_number = {}        # HASH MAP: slot_number -> Slot (O(1) lookup)
        self.active_sessions = {}       # HASH MAP: plate_number -> session dict
        self._load_from_db()

    # -- setup -------------------------------------------------------------
    def _load_from_db(self):
        self.slots.clear()
        self.queues.clear()
        self.slot_by_number.clear()
        self.active_sessions.clear()

        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM slots ORDER BY level, slot_type, id").fetchall()
            for row in rows:
                slot = Slot(
                    id=row["id"], slot_number=row["slot_number"], level=row["level"],
                    slot_type=row["slot_type"], status=row["status"], plate_number=row["plate_number"],
                )
                self.slots.append(slot)
                self.slot_by_number[slot.slot_number] = slot
                key = (slot.level, slot.slot_type)
                self.queues.setdefault(key, deque())
                if slot.status == "available":
                    self.queues[key].append(slot.slot_number)

            active = conn.execute(
                """SELECT s.*, sl.slot_number FROM sessions s
                   JOIN slots sl ON sl.id = s.slot_id
                   WHERE s.status = 'active'"""
            ).fetchall()
            for row in active:
                self.active_sessions[row["plate_number"]] = {
                    "session_id": row["id"],
                    "slot_id": row["slot_id"],
                    "slot_number": row["slot_number"],
                    "entry_time": row["entry_time"],
                    "vehicle_type": row["vehicle_type"],
                }

    # -- Module 1: read-only views -------------------------------------
    def get_slot_display(self):
        return self.slots

    def available_count(self):
        return sum(1 for s in self.slots if s.status == "available")

    def occupied_count(self):
        return sum(1 for s in self.slots if s.status == "occupied")

    def out_of_service_count(self):
        return sum(1 for s in self.slots if s.status == "out_of_service")

    def counts_by_type(self):
        """Available / total per slot_type, for the dashboard summary."""
        result = {}
        for t in ("regular", "vip", "accessible"):
            total = sum(1 for s in self.slots if s.slot_type == t)
            avail = sum(1 for s in self.slots if s.slot_type == t and s.status == "available")
            result[t] = {"available": avail, "total": total}
        return result

    # -- Module 2: Vehicle Entry ----------------------------------------
    def park_vehicle(self, plate_number: str, vehicle_type: str, slot_type: str, level: str = None):
        """
        Algorithm:
          1. Reject duplicate active plate.
          2. Reject unknown vehicle_type/slot_type.
          3. Pick a queue: an explicit level if given, else the first
             level (in config order) that still has a free slot of
             that type.
          4. Pop a free slot number from the FRONT of that queue - O(1).
          5. Insert session row, mark slot occupied (memory + DB).
        """
        plate_number = plate_number.strip().upper()

        if plate_number in self.active_sessions:
            return False, "This vehicle is already parked inside."
        if vehicle_type not in rate_manager.vehicle_types:
            return False, "Unknown vehicle type."

        chosen_key = None
        if level:
            key = (level, slot_type)
            if self.queues.get(key):
                chosen_key = key
        else:
            for key, q in self.queues.items():
                if key[1] == slot_type and q:
                    chosen_key = key
                    break

        if not chosen_key:
            return False, f"No {slot_type} slots available right now."

        slot_number = self.queues[chosen_key].popleft()
        slot = self.slot_by_number[slot_number]
        entry_time = datetime.now()

        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO sessions (plate_number, vehicle_type, slot_id, entry_time, status) "
                "VALUES (?, ?, ?, ?, 'active')",
                (plate_number, vehicle_type, slot.id, entry_time.isoformat()),
            )
            session_id = cur.lastrowid
            conn.execute(
                "UPDATE slots SET status='occupied', plate_number=? WHERE id=?",
                (plate_number, slot.id),
            )

        slot.status = "occupied"
        slot.plate_number = plate_number
        self.active_sessions[plate_number] = {
            "session_id": session_id,
            "slot_id": slot.id,
            "slot_number": slot.slot_number,
            "entry_time": entry_time.isoformat(),
            "vehicle_type": vehicle_type,
        }
        return True, slot

    # -- preview fee without ending the session --------------------------
    def preview_fee(self, plate_number: str):
        plate_number = plate_number.strip().upper()
        session = self.active_sessions.get(plate_number)
        if not session:
            return None
        slot = self.slot_by_number[session["slot_number"]]
        entry_time = datetime.fromisoformat(session["entry_time"])
        fee, minutes, vat_amount = calculate_fee(entry_time, datetime.now(), session["vehicle_type"], slot.slot_type)
        return {
            "plate_number": plate_number,
            "slot_number": slot.slot_number,
            "vehicle_type": session["vehicle_type"],
            "entry_time": entry_time,
            "minutes": round(minutes, 1),
            "fee": fee,
            "vat_amount": vat_amount,
        }

    # -- Exception handling: lost ticket / unreadable plate ---------------
    def find_active_plate_by_slot(self, slot_number: str):
        """
        Exception-handling module: if an attendant can't read a plate
        (faded, obscured, driver's ticket lost) but knows which bay the
        vehicle is in, this resolves bay -> plate so the normal
        checkout flow can proceed unchanged. O(n) over active sessions,
        which in practice is small (bounded by total slot count).
        """
        slot_number = slot_number.strip().upper()
        for plate, session in self.active_sessions.items():
            if session["slot_number"] == slot_number:
                return plate
        return None

    # -- Module 4: Vehicle Exit + Payment + Barrier ------------------------
    def checkout_vehicle(self, plate_number: str, payment_method: str):
        """Synchronous checkout: cash, card, and simulated M-Pesa all
        complete immediately, in one request. Real M-Pesa (Daraja STK
        push) uses initiate_mpesa_checkout() / confirm_mpesa_payment()
        instead, because a real payment is asynchronous - it's
        confirmed later, by Safaricom's callback, not by this call."""
        plate_number = plate_number.strip().upper()
        session = self.active_sessions.get(plate_number)
        if not session:
            return False, "No active session found for this plate."

        slot = self.slot_by_number[session["slot_number"]]
        entry_time = datetime.fromisoformat(session["entry_time"])
        exit_time = datetime.now()
        fee, minutes, vat_amount = calculate_fee(entry_time, exit_time, session["vehicle_type"], slot.slot_type)
        payment_ref = simulate_payment(payment_method, fee)

        return True, self._finalize_checkout(plate_number, fee, vat_amount, payment_method, payment_ref, exit_time, minutes)

    def _finalize_checkout(self, plate_number, fee, vat_amount, payment_method, payment_ref, exit_time, minutes):
        """Shared completion step: mark the session completed, free the
        slot, and update in-memory state. Used by both the synchronous
        path (checkout_vehicle) and the async M-Pesa callback path
        (confirm_mpesa_payment) so the two never drift apart."""
        session = self.active_sessions[plate_number]
        slot = self.slot_by_number[session["slot_number"]]
        entry_time = datetime.fromisoformat(session["entry_time"])

        with get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET exit_time=?, fee=?, vat_amount=?, payment_method=?, payment_ref=?, "
                "payment_status='confirmed', status='completed' WHERE id=?",
                (exit_time.isoformat(), fee, vat_amount, payment_method, payment_ref, session["session_id"]),
            )
            conn.execute("UPDATE slots SET status='available', plate_number=NULL WHERE id=?", (session["slot_id"],))

        slot.status = "available"
        slot.plate_number = None
        self.queues.setdefault((slot.level, slot.slot_type), deque()).append(slot.slot_number)
        del self.active_sessions[plate_number]

        return {
            "plate_number": plate_number, "slot_number": slot.slot_number,
            "vehicle_type": session["vehicle_type"], "entry_time": entry_time, "exit_time": exit_time,
            "minutes": round(minutes, 1), "fee": fee, "vat_amount": vat_amount,
            "payment_method": payment_method, "payment_ref": payment_ref, "barrier": "OPEN",
        }

    # -- Real M-Pesa (Daraja STK push): initiate ---------------------------
    def initiate_mpesa_checkout(self, plate_number: str, phone_number: str):
        """
        Starts a REAL M-Pesa payment. Computes the fee now (so the
        amount requested matches what's shown to the driver) and
        records it against the session with payment_status='pending',
        then sends the STK push. The vehicle stays parked - the slot
        is only freed once confirm_mpesa_payment() runs, on Safaricom's
        callback. Returns (True, {..., "checkout_request_id": ...}) or
        (False, error_message).
        """
        import mpesa  # imported lazily so a missing `requests` package
                       # never breaks the rest of the app if Daraja is unused

        plate_number = plate_number.strip().upper()
        session = self.active_sessions.get(plate_number)
        if not session:
            return False, "No active session found for this plate."

        phone = mpesa.normalise_phone(phone_number)
        if not phone:
            return False, "Enter a valid Kenyan phone number, e.g. 07XXXXXXXX."

        slot = self.slot_by_number[session["slot_number"]]
        entry_time = datetime.fromisoformat(session["entry_time"])
        fee, minutes, vat_amount = calculate_fee(entry_time, datetime.now(), session["vehicle_type"], slot.slot_type)

        try:
            result = mpesa.initiate_stk_push(
                phone_number=phone, amount=fee,
                account_reference=slot.slot_number, transaction_desc="Parking fee",
            )
        except Exception as e:  # network error, bad credentials, Daraja downtime, etc.
            return False, f"Could not reach M-Pesa: {e}"

        checkout_request_id = result.get("CheckoutRequestID")
        if not checkout_request_id:
            return False, result.get("errorMessage", "M-Pesa did not accept the request.")

        with get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET fee=?, vat_amount=?, payment_method='mpesa', "
                "payment_status='pending', mpesa_checkout_request_id=? WHERE id=?",
                (fee, vat_amount, checkout_request_id, session["session_id"]),
            )

        return True, {
            "plate_number": plate_number, "fee": fee, "vat_amount": vat_amount,
            "checkout_request_id": checkout_request_id, "phone_number": phone,
        }

    # -- Real M-Pesa (Daraja STK push): confirm, called from the callback --
    def confirm_mpesa_payment(self, checkout_request_id: str, success: bool, receipt_number: str = None):
        """Called by the /mpesa/callback route when Safaricom reports
        the result of an STK push. Looks up which active session this
        checkout_request_id belongs to (by database, since the app may
        have restarted since the push was sent) and either finalises
        the checkout (success) or marks the attempt failed so the
        attendant can retry (failure)."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT s.*, sl.slot_number FROM sessions s JOIN slots sl ON sl.id = s.slot_id "
                "WHERE s.mpesa_checkout_request_id=? AND s.status='active'",
                (checkout_request_id,),
            ).fetchone()

        if not row:
            return False, "No matching pending payment found (already handled, or unknown request)."

        plate_number = row["plate_number"]
        if plate_number not in self.active_sessions:
            return False, "Session is no longer active in memory (server may have restarted)."

        if not success:
            with get_conn() as conn:
                conn.execute("UPDATE sessions SET payment_status='failed' WHERE id=?", (row["id"],))
            return True, {"plate_number": plate_number, "status": "failed"}

        exit_time = datetime.now()
        entry_time = datetime.fromisoformat(row["entry_time"])
        minutes = (exit_time - entry_time).total_seconds() / 60
        payment_ref = receipt_number or f"MPESA-{checkout_request_id[-8:]}"

        receipt = self._finalize_checkout(plate_number, row["fee"], row["vat_amount"], "mpesa", payment_ref, exit_time, minutes)
        return True, {"plate_number": plate_number, "status": "confirmed", "receipt": receipt}

    def get_payment_status(self, plate_number: str):
        """Polled by the 'waiting for payment' screen. Returns
        'pending', 'confirmed' (session now completed - look up its
        receipt separately), 'failed', or None if there's no session
        and no recent payment attempt at all for this plate."""
        plate_number = plate_number.strip().upper()
        if plate_number in self.active_sessions:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT payment_status FROM sessions WHERE id=?",
                    (self.active_sessions[plate_number]["session_id"],),
                ).fetchone()
            return row["payment_status"] if row else None
        # No longer active - most likely just confirmed and completed
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id, payment_status, status FROM sessions WHERE plate_number=? ORDER BY id DESC LIMIT 1",
                (plate_number,),
            ).fetchone()
        if row and row["status"] == "completed":
            return "confirmed"
        return row["payment_status"] if row else None

    # -- Admin: take a slot out of service / bring it back -----------------
    def set_slot_service_state(self, slot_number: str, in_service: bool):
        slot = self.slot_by_number.get(slot_number)
        if not slot:
            return False, "Unknown slot."
        if slot.status == "occupied":
            return False, "Cannot change a slot that is currently occupied."

        new_status = "available" if in_service else "out_of_service"
        with get_conn() as conn:
            conn.execute("UPDATE slots SET status=? WHERE id=?", (new_status, slot.id))

        key = (slot.level, slot.slot_type)
        q = self.queues.setdefault(key, deque())
        if in_service and slot.slot_number not in q:
            q.append(slot.slot_number)
        elif not in_service and slot.slot_number in q:
            q.remove(slot.slot_number)

        slot.status = new_status
        return True, new_status
