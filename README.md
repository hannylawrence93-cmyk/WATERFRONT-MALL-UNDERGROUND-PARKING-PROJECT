# Waterfront Mall Underground Parking System

A modern, multi-user parking management system built for Waterfront Mall's
underground parking, per the client brief below.
Same core idea — drivers see live slot availability, vehicles are
recorded on arrival, and on exit the system calculates duration + fee
before the barrier opens — but built out into something closer to a
real deployable system.

Stack: **Python 3 + Flask** + **SQLite**.

---

## Assignment brief — where each requirement is answered

This system was built against the brief: *"develop a modern parking
system... drivers see (visual display) of parking slots available
before entry... system records vehicles on arrival... on exit,
automatically calculates total time spent and amount to pay...
barrier opens on payment."* Specifically:

| Brief requirement | Where it's answered |
|---|---|
| Critically analyse the client's terms of reference to propose modules | See **"Use cases identified"** and **"Modules proposed"** below |
| **(a)** Algorithm for each module | See **"Algorithms"** below, and docstrings in `algorithms.py` |
| **(b)** Data structures and reasons for their use | See **"Data structures, and why"** below |
| **(c)** Dynamic database design | See **"Database schema"** below |
| Visual display of slots before entry | Public board at `/` — no login needed, live-updating grid |
| Record vehicles on arrival | `SlotManager.park_vehicle()` in `algorithms.py` |
| Auto-calculate duration + fee on exit | `calculate_fee()` in `algorithms.py` |
| Fee tiers (free ≤30min, 50 ≤2h, 100 ≤4h, 300 ≤6h, 500 beyond) | These are the **first-run defaults** for the "car" type in `config.py`; the live values are in the database and editable at `/admin/rates` |
| Barrier opens on payment | `checkout_vehicle()` only returns `"barrier": "OPEN"` after payment is confirmed |
| Well-commented, web-based | Flask + Jinja, every module has docstrings explaining *why*, not just *what* |
| Appropriate name | **Waterfront Mall Underground Parking System** |
| Live slot board at entrance + web/mobile view | See **Objective 1.2** table below |
| Change rates without a software change | See **Objective 1.2** table below |
| Auditable record for reconciliation and VAT | See **Objective 1.2** table below |
| Scope (in/out) | See **Scope 1.3** below |

## 1.2 Objectives — where each is answered

| Objective (from brief 1.2) | Where it's answered |
|---|---|
| Show live slot availability on a display board at the entrance and in a web/mobile view | Public board at `/` (no login) — same live grid works as an entrance display screen *and* a normal web page; responsive layout also works on mobile browsers |
| Record each vehicle on arrival, with plate, time of entry, and allocated bay | `SlotManager.park_vehicle()` in `algorithms.py`; stored in the `sessions` table |
| Calculate duration and amount payable automatically at exit | `calculate_fee()` in `algorithms.py`, shown live on `/checkout` before payment |
| Collect payment by M-Pesa, card or cash, and open the barrier only on confirmed payment | Cash/card/simulated M-Pesa: `simulate_payment()`. **Real M-Pesa**: a genuine Daraja STK push via `mpesa.py`, confirmed asynchronously by Safaricom's callback (`/mpesa/callback`) — see Section 6.1. Either way, `"barrier": "OPEN"` is only ever returned after payment is confirmed. |
| **Let management change parking rates at any time, without a software change** | **`/admin/rates`** (manager-only) — fee tiers, VIP surcharge and VAT rate are **data in the database** (`settings` table), edited from the browser. `calculate_fee()` reads the live value on every call. No code edit, no redeploy, no restart. See `RateManager` in `algorithms.py`. |
| **Produce an auditable record of every shilling collected, for reconciliation and VAT** | **`/reconciliation`** (manager-only) — every completed session stores `fee`, `vat_amount` and `payment_ref` the instant it's paid (append-only `sessions` table). The report breaks totals down by payment method and by transaction, with a CSV export built for handing to an accountant. |

## 1.3 Scope — what's built vs. deliberately left out

**In scope, and built:**

- Entry lane control — `park_vehicle()`, the public/staff displays
- Slot monitoring and display — live public board + staff dashboard
- Slot allocation — per-(level, slot type) FIFO queues
- Duration and fee computation — `calculate_fee()`, now with live, manager-editable rates
- Payment collection — cash / M-Pesa / card simulation
- Exit barrier control — simulated, gated on confirmed payment
- **Exception handling** — duplicate active-plate entries are rejected; requesting a slot type/level with nothing free is rejected with a clear message; a slot can't be reactivated while occupied; and a **lost ticket / unreadable plate** can be resolved by looking the vehicle up by its bay number instead (`/checkout?slot_number=...`, see `find_active_plate_by_slot()`)
- Administrative reporting — History (filterable + CSV), Analytics, Reconciliation (VAT-aware)

**Explicitly out of scope for this phase** (per the brief, not attempted):

- Online pre-booking of bays
- Valet operations
- Integration with third-party loyalty schemes
- Automated number-plate recognition / blacklisting (manual entry only — see "What to extend next" for how this would plug in later)

## Use cases identified

| Actor | Use case |
|-------|----------|
| Driver | View available slots before entering (public board, no login) |
| Attendant | Record vehicle arrival, allocate a slot, by vehicle type and slot type |
| Driver / Attendant | Request exit, view fee owed |
| System | Calculate fee based on duration, vehicle type, and slot type |
| Driver / Attendant | Confirm payment (cash / M-Pesa / card) |
| Barrier | Open on successful payment |
| Attendant | Search/filter session history |
| Manager | View analytics (revenue, occupancy, breakdowns) |
| Manager | View reconciliation report (gross / VAT / net, by payment method) |
| Manager | Change parking rates, VIP surcharge, VAT rate — no code change |
| Manager | Take a slot out of service / reactivate it |
| Manager | Create new staff accounts |
| Attendant | Look up a vehicle by bay number when the plate/ticket is unreadable (exception handling) |

## Modules proposed

1. **Slot Management Module** — tracks every slot's state (available /
   occupied / out of service) across levels and slot types, and drives
   both the public and staff visual displays.
2. **Vehicle Entry Module** — validates and records an arriving
   vehicle, allocates it a slot of the requested type/level.
3. **Fee Calculation Module** — pure function turning
   (entry_time, exit_time, vehicle_type, slot_type) into a fee.
4. **Vehicle Exit & Payment Module** — looks up the active session,
   shows the fee, simulates payment, finalises the session.
5. **Barrier Control Module** — simulated: returns `"OPEN"` only after
   payment is confirmed. On real hardware this would send a signal to
   a relay/controller instead.
6. **Auth Module** — separates public (driver) access from
   authenticated staff actions, with role separation for managers.
7. **Rate Management Module** — lets a manager change fee tiers, VIP
   surcharge and VAT rate from the browser at any time, with zero code
   changes (Objective 1.2). See `RateManager` in `algorithms.py`.
8. **Exception Handling Module** — covers duplicate entries, no-slots-
   available, occupied-slot admin actions, and the lost-ticket case
   (resolve a vehicle by bay number instead of plate).

---

## What's new vs. the original

| Area | v1 | v2 |
|------|----|----|
| Driver view | Same page as staff, no login | **Public board at `/`** (no login) — the "driver sees availability before entry" requirement — shows only slot status, no plate numbers |
| Accounts | None (anyone can use it) | Staff tools live behind a login at `/dashboard`, two roles: **attendant** and **manager** |
| Vehicle types | One flat fee table | Car / Motorcycle / Truck, each with its own fee tiers |
| Slot types | One pool of identical slots | Regular, **VIP** (surcharge), **Accessible** bays, tracked separately |
| Layout | Single flat list of slots | **Multi-level** (L1, L2, ...), configurable per level |
| Payments | "Assume paid on confirm" | Cash / **M-Pesa** (simulated) / Card, each generates a payment reference |
| Dashboard | Static, reload to refresh | **Live-polling** grid (updates counts & slot colors every few seconds without a page reload) |
| Slot admin | Only by editing `TOTAL_SLOTS` in code | Manager UI to take a slot **out of service** / reactivate it |
| History | Last 100 rows, no filtering | Searchable/filterable by plate, status, vehicle type, date range, plus **CSV export** |
| Reporting | None | **Analytics dashboard** (manager-only): revenue over time, revenue by vehicle type, occupancy %, per-level breakdown, charts |
| Receipts | Shown once | Re-viewable later from History, printable |
| Users admin | N/A | Manager can create new attendant/manager accounts |

---

## 1. Public board vs. staff dashboard

The brief asks for drivers to see a **visual display of available slots
before entry**. That's a public-facing requirement — a driver arriving
at the gate has no account, so it has to work without login. v2 splits
the UI accordingly:

- **`/` (public board)** — no login. Shows the live slot grid and
  counts, meant for a screen mounted at the entrance. Deliberately
  does **not** show plate numbers — that's the attendant's business,
  not something to broadcast to anyone walking past.
- **`/dashboard` (staff)** — requires login. Same grid, but with plate
  numbers, plus the vehicle-entry form and links to Exit/Pay, History,
  and (for managers) Analytics, Slot admin and User admin.

Both poll a JSON endpoint every few seconds (`/api/public-slots` and
`/api/slots` respectively) to stay live without full page reloads.

## 2. Roles

- **Attendant** — dashboard, vehicle entry, exit/pay, history (view + filter).
- **Manager** — everything an attendant can do, plus Analytics, Slot admin, User admin, and CSV export.

Demo accounts (created automatically on first run — **change these before
any real deployment**):

| Username | Password | Role |
|----------|----------|------|
| `manager` | `manager123` | manager |
| `attendant` | `attendant123` | attendant |

## 3. Algorithms (one per module, per requirement (a))

1. **Slot Management** — `SlotManager` in `algorithms.py`. One `deque`
   (FIFO queue) **per (level, slot type)**, so a VIP request can never
   accidentally consume a regular bay. Allocate = `popleft()`,
   release = `append()`, both O(1).
2. **Vehicle Entry** — `SlotManager.park_vehicle()`. Rejects duplicate
   active plates, picks a queue (explicit level or first level with a
   free slot of the requested type), pops a slot, writes the session.
3. **Fee Calculation** — `calculate_fee()`. Each vehicle type has its
   own tiered band table (see `config.py`), plus a flat VIP surcharge.
   Still O(1) — a fixed, auditable decision tree, not a per-minute rate.
4. **Vehicle Exit / Payment** — `SlotManager.checkout_vehicle()`. O(1)
   session lookup via a plate → session hash map, computes the fee,
   simulates a payment (see below), frees the slot.
5. **Payment simulation** — `simulate_payment()`. Generates a fake
   M-Pesa / card / cash reference. This is the one function you'd swap
   out for a real payment gateway (M-Pesa Daraja API, Stripe, etc.) —
   everything else in the app is unaffected by that change.
6. **Barrier Control** — simulated, same as v1 (`"barrier": "OPEN"` in
   the receipt). Swap for a GPIO/serial call to real hardware.

## 4. Data structures, and why (requirement (b))

| Structure | Used for | Why |
|-----------|----------|-----|
| `list` | Ordered slots for the visual grid | Matches the physical numbered layout |
| `dict` of `deque`, one per `(level, slot_type)` | Available slot numbers | O(1) allocate/release, FIFO fairness, and type/level isolation (a VIP queue never leaks a regular bay) |
| `dict` (`slot_by_number`) | Slot lookup by number | O(1) instead of scanning the list, used on every entry/exit/toggle |
| `dict` (`active_sessions`) | plate → session | O(1) lookup on exit instead of scanning the session log |
| `dict` (`RateManager.vehicle_types`) | vehicle_type → fee tiers | Loaded once from the `settings` table, refreshed instantly on save, so a rate change is O(1) to apply and O(1) to read — see Objective 1.2 |
| SQLite tables | Durable state + full history | Survives restarts, supports reporting, relational integrity via foreign keys |

## 5. Database schema (dynamic database, requirement (c))

```
users
-----
id, username (unique), password_hash, role ('attendant' | 'manager')

slots
-----
id, slot_number (unique, e.g. "L1-R03"), level, slot_type ('regular'|'vip'|'accessible'),
status ('available'|'occupied'|'out_of_service'), plate_number

sessions
--------
id, plate_number, vehicle_type, slot_id -> FK slots.id,
entry_time, exit_time, fee, vat_amount, payment_method, payment_ref,
status ('active'|'completed')

settings
--------
key (unique, e.g. "vehicle_types", "vip_surcharge", "vat_rate"), value (JSON)
```

`sessions` is append-only (your audit trail for free, and the source
of truth for the Reconciliation report). The layout in `config.py`
(`LEVELS`) can be edited — new level, more slots of a type — and
`ensure_slots()` will insert only the missing rows; existing slots,
sessions and users are never touched.

`settings` is what makes pricing **dynamic data instead of a
constant**: `config.py`'s `VEHICLE_TYPES` / `VIP_SURCHARGE` / `VAT_RATE`
only seed this table the very first time the app runs. After that,
`/admin/rates` reads and writes `settings` directly, and `RateManager`
keeps an in-memory copy in sync — satisfying Objective 1.2 ("change
parking rates at any time without a software change") at the database
level, not just the UI level.

## 6. How to run it (Windows)

Double-click `setup.bat` (first time only), then `start.bat` each time
you want to run it. Or from a terminal:

```powershell
cd parking_system_v2
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

If PowerShell blocks the activation step with an execution-policy
error, run this once in that PowerShell window and try again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Open **http://localhost:5000**, log in with one of the demo accounts
above. `parking.db` is created automatically on first run, in whichever
folder you launched the app from.

## 6.1 Real M-Pesa (Daraja) — optional

By default, all payment methods (including M-Pesa) use a **simulated**
payment reference — the app is fully functional with zero external
setup. To enable a **real** M-Pesa STK push instead:

1. Create a free account at [developer.safaricom.co.ke](https://developer.safaricom.co.ke)
   and create an app using the **"Lipa Na M-Pesa Sandbox"** product to
   get a Consumer Key and Consumer Secret.
2. Copy `.env.example` to `.env` and fill in `MPESA_CONSUMER_KEY` and
   `MPESA_CONSUMER_SECRET`. The sandbox shortcode and passkey are
   already filled in with Safaricom's public test values.
3. Safaricom's servers need to call **your** server back to confirm
   payment, so `localhost` isn't reachable — install
   [ngrok](https://ngrok.com) and run:
   ```
   ngrok http 5000
   ```
   Copy the `https://...ngrok-free.app` URL it gives you.
4. In `.env`, set:
   ```
   MPESA_CALLBACK_URL=https://your-ngrok-url.ngrok-free.app/mpesa/callback
   ```
5. Restart the app (`python app.py`). The checkout page will now show
   **"MPESA (real STK push)"** instead of "(simulated)", and ask for a
   phone number. In sandbox mode, Safaricom auto-confirms a few
   seconds after the push — no real phone is required to test the
   flow (Safaricom's standard sandbox test number is `254708374149`).

**How it works under the hood:** `initiate_mpesa_checkout()` computes
the fee and sends the STK push, but the session stays `active` with
`payment_status='pending'` — the vehicle is still parked, nothing is
finalised yet. Safaricom calls `/mpesa/callback` asynchronously
(seconds later) with the result; `confirm_mpesa_payment()` then either
completes the checkout (slot freed, barrier opens, receipt shown) or
marks the attempt `failed` so the attendant can retry with cash. The
attendant's screen polls `/api/payment-status/<plate>` every few
seconds while waiting. This two-step design exists because a real
payment is **asynchronous** — Daraja hands back a "request accepted"
response immediately, and the actual yes/no answer arrives later, on
its own schedule, as a separate callback.

If `MPESA_CONSUMER_KEY`/`MPESA_CONSUMER_SECRET`/`MPESA_CALLBACK_URL`
are not all set, `mpesa.is_configured()` returns `False` and every
payment method — including "M-Pesa" — quietly uses the simulated path
instead, so a missing `.env` file never breaks the app.

## 7. Configuring the system

Everything client-tunable lives in `config.py`:

- `LEVELS` — how many levels, and how many regular/VIP/accessible
  slots per level.
- `VEHICLE_TYPES` — add/remove vehicle types and their fee tiers.
- `VIP_SURCHARGE` — flat Kshs added on top of the tiered fee for VIP
  bays.
- `PAYMENT_METHODS` — which payment options show up at checkout.
- `SEED_USERS` — accounts created on first run.

## 8. What to extend next

- ~~Real payment gateway (M-Pesa Daraja API)~~ — **done**, see Section
  6.1. A card gateway (Stripe/Pesapal) could follow the same pattern:
  `is_configured()` fallback + an async confirm step.
- Number-plate recognition (ANPR) camera feeding straight into
  `park_vehicle()` instead of manual entry.
- Real barrier hardware: replace the `"barrier": "OPEN"` string with a
  GPIO/serial signal to an actual relay.
- Multi-branch support: add a `branch_id` column and partition
  everything (queues, users, reports) per branch.
- Reservations: let a driver reserve a specific slot ahead of arrival.
- WebSockets instead of polling for a truly real-time dashboard.

## 9. Pushing to GitHub

```bash
git init
git add .
git commit -m "Waterfront Mall Underground Parking System"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```

(`.gitignore` already excludes `parking.db`, `venv/` and `__pycache__/`.)
