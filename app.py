"""
app.py
------
Web layer (Flask). Ties database + algorithms + auth together.

Routes
------
GET  /                                        -> PUBLIC live slot board (no
                                                  login) - what a driver sees
                                                  before entering, no plates
GET  /api/public-slots                        -> JSON status feed for the
                                                  public board (no plates)
GET  /login, POST /login, GET /logout         -> auth
GET  /dashboard                               -> staff dashboard: full slot
                                                  grid (with plates) + entry
                                                  form. Requires login.
GET  /api/slots                               -> JSON status feed (with
                                                  plates), polled by the
                                                  staff dashboard for a live
                                                  feel without full reloads
POST /entry                                   -> Vehicle Entry module
GET  /checkout                                -> preview fee for a plate
                                                  (or a bay number, for the
                                                  lost-ticket exception case)
POST /checkout/confirm                        -> Exit + Payment + Barrier
GET  /receipt/<id>                            -> re-view a past receipt
GET  /history                                 -> searchable session log
GET  /history/export.csv                      -> CSV export of the log
GET  /analytics                               -> manager-only reports
GET  /reconciliation                          -> manager-only: gross/VAT/net
                                                  breakdown for accounting
GET  /reconciliation/export.csv               -> CSV of the above
GET  /admin/rates, POST /admin/rates/update   -> manager-only: change fee
                                                  tiers, VIP surcharge, VAT
                                                  rate with NO code change
GET  /admin/slots, POST /admin/slots/toggle   -> manager-only slot admin
GET  /admin/users, POST /admin/users          -> manager-only user admin
"""

import csv
import io
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    jsonify, Response,
)
from werkzeug.security import check_password_hash, generate_password_hash

from database import init_db, get_conn
from auth import login_required, roles_required
from config import SLOT_TYPES, LEVELS, PAYMENT_METHODS, SECRET_KEY, ROLES

# init_db() must run before algorithms.py is imported: algorithms.py
# constructs a module-level RateManager that reads the `settings`
# table on import, so the table needs to exist first.
init_db()

from algorithms import SlotManager, rate_manager  # noqa: E402
import mpesa  # noqa: E402

app = Flask(__name__)
app.secret_key = SECRET_KEY

manager = SlotManager()


@app.context_processor
def inject_globals():
    return {
        "current_user": session.get("username"),
        "current_role": session.get("role"),
        "vehicle_types": rate_manager.vehicle_types,  # live rates, not the config.py defaults
        "slot_types": SLOT_TYPES,
        "levels": list(LEVELS.keys()),
        "payment_methods": PAYMENT_METHODS,
        "mpesa_configured": mpesa.is_configured(),
    }


# ---------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_conn() as conn:
            user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            flash(f"Welcome back, {user['username']}.", "success")
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------
# Public slot display — this is the "driver sees availability before
# entry" requirement from the brief. No login needed: it's meant to be
# shown on a screen at the gate/entrance, so a driver can check space
# before ever approaching an attendant. It deliberately does NOT show
# plate numbers - that's only relevant to staff, and broadcasting other
# people's plates on a public screen is an unnecessary privacy leak.
# ---------------------------------------------------------------------
@app.route("/")
def public_display():
    slots = manager.get_slot_display()
    return render_template(
        "public.html",
        slots=slots,
        available=manager.available_count(),
        occupied=manager.occupied_count(),
        total=len(slots),
        by_type=manager.counts_by_type(),
    )


@app.route("/api/public-slots")
def api_public_slots():
    """JSON feed for the public display - status only, no plate numbers."""
    slots = manager.get_slot_display()
    return jsonify({
        "available": manager.available_count(),
        "occupied": manager.occupied_count(),
        "total": len(slots),
        "slots": [
            {"slot_number": s.slot_number, "level": s.level, "slot_type": s.slot_type, "status": s.status}
            for s in slots
        ],
    })


# ---------------------------------------------------------------------
# Staff dashboard — entry form, full slot grid with plate numbers,
# and links into the rest of the staff-only tools. Requires login.
# ---------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    slots = manager.get_slot_display()
    return render_template(
        "index.html",
        slots=slots,
        available=manager.available_count(),
        occupied=manager.occupied_count(),
        out_of_service=manager.out_of_service_count(),
        total=len(slots),
        by_type=manager.counts_by_type(),
    )


@app.route("/api/slots")
@login_required
def api_slots():
    """JSON feed the staff dashboard polls every few seconds for a live
    grid (with plate numbers), without a full page reload."""
    slots = manager.get_slot_display()
    return jsonify({
        "available": manager.available_count(),
        "occupied": manager.occupied_count(),
        "out_of_service": manager.out_of_service_count(),
        "total": len(slots),
        "slots": [
            {
                "slot_number": s.slot_number, "level": s.level, "slot_type": s.slot_type,
                "status": s.status, "plate_number": s.plate_number,
            }
            for s in slots
        ],
    })


# ---------------------------------------------------------------------
# Vehicle Entry
# ---------------------------------------------------------------------
@app.route("/entry", methods=["POST"])
@login_required
def entry():
    plate = request.form.get("plate_number", "")
    vehicle_type = request.form.get("vehicle_type", "car")
    slot_type = request.form.get("slot_type", "regular")
    level = request.form.get("level") or None

    if not plate.strip():
        flash("Please enter a number plate.", "error")
        return redirect(url_for("dashboard"))

    ok, result = manager.park_vehicle(plate, vehicle_type, slot_type, level)
    if ok:
        flash(f"Vehicle {plate.strip().upper()} parked in slot {result.slot_number}.", "success")
    else:
        flash(result, "error")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------
# Vehicle Exit / Payment
# ---------------------------------------------------------------------
@app.route("/checkout", methods=["GET"])
@login_required
def checkout_preview():
    plate = request.args.get("plate_number", "")
    slot_number = request.args.get("slot_number", "")

    # Exception handling: lost ticket / unreadable plate. If the
    # attendant knows the bay instead of the plate, resolve it here and
    # fall through to the same preview logic as a normal lookup.
    resolved_via_slot = False
    if not plate and slot_number:
        found_plate = manager.find_active_plate_by_slot(slot_number)
        if found_plate:
            plate = found_plate
            resolved_via_slot = True
        else:
            flash(f"No active vehicle found in bay {slot_number.strip().upper()}.", "error")

    preview = manager.preview_fee(plate) if plate else None
    if plate and not preview:
        flash("No active session found for that plate.", "error")
    return render_template("checkout.html", preview=preview, plate=plate, resolved_via_slot=resolved_via_slot)


@app.route("/checkout/confirm", methods=["POST"])
@login_required
def checkout_confirm():
    plate = request.form.get("plate_number", "")
    payment_method = request.form.get("payment_method", "cash")
    ok, result = manager.checkout_vehicle(plate, payment_method)
    if not ok:
        flash(result, "error")
        return redirect(url_for("dashboard"))
    return render_template("receipt.html", receipt=result)


# ---------------------------------------------------------------------
# Real M-Pesa (Daraja STK push) — only reachable when mpesa.is_configured()
# is True; the "M-Pesa" option on the checkout form otherwise uses the
# instant simulated path above (checkout_confirm), so the whole app
# remains fully functional with zero external setup.
# ---------------------------------------------------------------------
@app.route("/checkout/mpesa/initiate", methods=["POST"])
@login_required
def checkout_mpesa_initiate():
    if not mpesa.is_configured():
        flash("M-Pesa is not configured on this server — see .env.example.", "error")
        return redirect(url_for("dashboard"))

    plate = request.form.get("plate_number", "")
    phone_number = request.form.get("phone_number", "")
    ok, result = manager.initiate_mpesa_checkout(plate, phone_number)
    if not ok:
        flash(result, "error")
        return redirect(url_for("checkout_preview", plate_number=plate))
    return render_template("mpesa_waiting.html", plate_number=plate, fee=result["fee"], phone_number=result["phone_number"])


@app.route("/mpesa/callback", methods=["POST"])
def mpesa_callback():
    """Public route — no login. Safaricom's servers call this, not a
    logged-in user, so it cannot sit behind @login_required. Daraja's
    callback payload shape (documented by Safaricom):
    {"Body": {"stkCallback": {"CheckoutRequestID": ..., "ResultCode": 0
    on success, "CallbackMetadata": {"Item": [...]} on success}}}"""
    payload = request.get_json(silent=True) or {}
    callback = payload.get("Body", {}).get("stkCallback", {})
    checkout_request_id = callback.get("CheckoutRequestID")
    result_code = callback.get("ResultCode")

    receipt_number = None
    if result_code == 0:
        for item in callback.get("CallbackMetadata", {}).get("Item", []):
            if item.get("Name") == "MpesaReceiptNumber":
                receipt_number = item.get("Value")

    if checkout_request_id:
        manager.confirm_mpesa_payment(checkout_request_id, success=(result_code == 0), receipt_number=receipt_number)

    # Safaricom only checks that we respond 200 OK — it does not render this.
    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})


@app.route("/api/payment-status/<plate_number>")
@login_required
def api_payment_status(plate_number):
    """Polled by the 'waiting for payment' screen every couple of
    seconds until it sees 'confirmed' or 'failed'."""
    status = manager.get_payment_status(plate_number)
    return jsonify({"status": status})


@app.route("/receipt/by-plate/<plate_number>")
@login_required
def receipt_by_plate(plate_number):
    """After an M-Pesa payment is confirmed asynchronously, the waiting
    page redirects here to show the same receipt the synchronous flow
    shows, looked up by the most recent completed session for that plate."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT s.*, sl.slot_number FROM sessions s JOIN slots sl ON sl.id = s.slot_id
               WHERE s.plate_number=? AND s.status='completed' ORDER BY s.id DESC LIMIT 1""",
            (plate_number.strip().upper(),),
        ).fetchone()
    if not row:
        flash("Receipt not found.", "error")
        return redirect(url_for("dashboard"))
    return redirect(url_for("receipt_view", session_id=row["id"]))


@app.route("/receipt/<int:session_id>")
@login_required
def receipt_view(session_id):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT s.*, sl.slot_number FROM sessions s
               JOIN slots sl ON sl.id = s.slot_id WHERE s.id=?""",
            (session_id,),
        ).fetchone()
    if not row or row["status"] != "completed":
        flash("Receipt not found.", "error")
        return redirect(url_for("history"))
    receipt = {
        "plate_number": row["plate_number"], "slot_number": row["slot_number"],
        "vehicle_type": row["vehicle_type"],
        "entry_time": datetime.fromisoformat(row["entry_time"]),
        "exit_time": datetime.fromisoformat(row["exit_time"]),
        "minutes": round((datetime.fromisoformat(row["exit_time"]) - datetime.fromisoformat(row["entry_time"])).total_seconds() / 60, 1),
        "fee": row["fee"], "vat_amount": row["vat_amount"] if row["vat_amount"] is not None else 0.0,
        "payment_method": row["payment_method"], "payment_ref": row["payment_ref"],
        "barrier": "OPEN",
    }
    return render_template("receipt.html", receipt=receipt)


# ---------------------------------------------------------------------
# History (search / filter / export)
# ---------------------------------------------------------------------
def _history_query(args):
    clauses, params = [], []
    plate = args.get("plate", "").strip().upper()
    status = args.get("status", "")
    vehicle_type = args.get("vehicle_type", "")
    date_from = args.get("date_from", "")
    date_to = args.get("date_to", "")

    if plate:
        clauses.append("s.plate_number LIKE ?")
        params.append(f"%{plate}%")
    if status:
        clauses.append("s.status = ?")
        params.append(status)
    if vehicle_type:
        clauses.append("s.vehicle_type = ?")
        params.append(vehicle_type)
    if date_from:
        clauses.append("date(s.entry_time) >= date(?)")
        params.append(date_from)
    if date_to:
        clauses.append("date(s.entry_time) <= date(?)")
        params.append(date_to)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT s.id, s.plate_number, s.vehicle_type, sl.slot_number, sl.level,
               s.entry_time, s.exit_time, s.fee, s.vat_amount, s.payment_method, s.payment_ref, s.status
        FROM sessions s
        JOIN slots sl ON sl.id = s.slot_id
        {where}
        ORDER BY s.id DESC
        LIMIT 500
    """
    return sql, params


@app.route("/history")
@login_required
def history():
    sql, params = _history_query(request.args)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return render_template("history.html", rows=rows, filters=request.args)


@app.route("/history/export.csv")
@roles_required("manager")
def history_export():
    sql, params = _history_query(request.args)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Plate", "Vehicle Type", "Slot", "Level", "Entry", "Exit", "Fee", "VAT", "Payment Method", "Payment Ref", "Status"])
    for r in rows:
        writer.writerow([r["plate_number"], r["vehicle_type"], r["slot_number"], r["level"],
                          r["entry_time"], r["exit_time"] or "", r["fee"] if r["fee"] is not None else "",
                          r["vat_amount"] if r["vat_amount"] is not None else "",
                          r["payment_method"] or "", r["payment_ref"] or "", r["status"]])
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=parking_history.csv"},
    )


# ---------------------------------------------------------------------
# Analytics (manager only)
# ---------------------------------------------------------------------
@app.route("/analytics")
@roles_required("manager")
def analytics():
    with get_conn() as conn:
        totals = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(fee),0) revenue, COALESCE(AVG(fee),0) avg_fee "
            "FROM sessions WHERE status='completed'"
        ).fetchone()

        by_vehicle = conn.execute(
            "SELECT vehicle_type, COUNT(*) c, COALESCE(SUM(fee),0) revenue "
            "FROM sessions WHERE status='completed' GROUP BY vehicle_type"
        ).fetchall()

        since = (datetime.now() - timedelta(days=6)).date().isoformat()
        by_day = conn.execute(
            "SELECT date(entry_time) d, COUNT(*) c, COALESCE(SUM(fee),0) revenue "
            "FROM sessions WHERE status='completed' AND date(entry_time) >= ? "
            "GROUP BY date(entry_time) ORDER BY d",
            (since,),
        ).fetchall()

        by_level = conn.execute(
            "SELECT sl.level, COUNT(*) c, COALESCE(SUM(s.fee),0) revenue "
            "FROM sessions s JOIN slots sl ON sl.id = s.slot_id "
            "WHERE s.status='completed' GROUP BY sl.level"
        ).fetchall()

    return render_template(
        "analytics.html",
        totals=totals, by_vehicle=by_vehicle, by_day=by_day, by_level=by_level,
        occupancy_pct=round(100 * manager.occupied_count() / max(len(manager.get_slot_display()), 1), 1),
    )


# ---------------------------------------------------------------------
# Reconciliation (manager only) — Objective: "produce an auditable
# record of every shilling collected, for reconciliation and VAT."
# Every completed session already stores fee + vat_amount + payment_ref
# at the moment it was paid (algorithms.checkout_vehicle), so this is
# a read-only report over that append-only ledger, not a recomputation.
# ---------------------------------------------------------------------
def _reconciliation_query(args):
    clauses, params = ["s.status = 'completed'"], []
    date_from = args.get("date_from", "")
    date_to = args.get("date_to", "")
    payment_method = args.get("payment_method", "")

    if date_from:
        clauses.append("date(s.exit_time) >= date(?)")
        params.append(date_from)
    if date_to:
        clauses.append("date(s.exit_time) <= date(?)")
        params.append(date_to)
    if payment_method:
        clauses.append("s.payment_method = ?")
        params.append(payment_method)

    where = f"WHERE {' AND '.join(clauses)}"
    sql = f"""
        SELECT s.id, s.plate_number, s.vehicle_type, sl.slot_number, s.exit_time,
               s.fee, s.vat_amount, s.payment_method, s.payment_ref
        FROM sessions s
        JOIN slots sl ON sl.id = s.slot_id
        {where}
        ORDER BY s.exit_time DESC
        LIMIT 1000
    """
    return sql, params


@app.route("/reconciliation")
@roles_required("manager")
def reconciliation():
    sql, params = _reconciliation_query(request.args)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        by_method = conn.execute(
            """SELECT payment_method, COUNT(*) c, COALESCE(SUM(fee),0) gross, COALESCE(SUM(vat_amount),0) vat
               FROM sessions WHERE status='completed' GROUP BY payment_method"""
        ).fetchall()

    gross_total = sum(r["fee"] or 0 for r in rows)
    vat_total = sum(r["vat_amount"] or 0 for r in rows)
    net_total = gross_total - vat_total

    return render_template(
        "reconciliation.html", rows=rows, filters=request.args, by_method=by_method,
        gross_total=round(gross_total, 2), vat_total=round(vat_total, 2), net_total=round(net_total, 2),
        vat_rate=rate_manager.vat_rate,
    )


@app.route("/reconciliation/export.csv")
@roles_required("manager")
def reconciliation_export():
    sql, params = _reconciliation_query(request.args)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Transaction ID", "Plate", "Vehicle Type", "Slot", "Paid At", "Gross (Kshs)", "VAT (Kshs)", "Net (Kshs)", "Payment Method", "Payment Ref"])
    for r in rows:
        gross = r["fee"] or 0
        vat = r["vat_amount"] or 0
        writer.writerow([r["id"], r["plate_number"], r["vehicle_type"], r["slot_number"], r["exit_time"],
                          f"{gross:.2f}", f"{vat:.2f}", f"{gross - vat:.2f}", r["payment_method"], r["payment_ref"]])
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=reconciliation.csv"},
    )


# ---------------------------------------------------------------------
# Admin: rates (manager only) — Objective 1.2: "let management change
# parking rates at any time without a software change." Fee tiers, the
# VIP surcharge and the VAT rate are edited here and take effect on the
# very next fee calculation - no code change, no restart.
# ---------------------------------------------------------------------
@app.route("/admin/rates")
@roles_required("manager")
def admin_rates():
    return render_template(
        "admin_rates.html",
        vehicle_types=rate_manager.vehicle_types,
        vip_surcharge=rate_manager.vip_surcharge,
        vat_rate=rate_manager.vat_rate,
    )


@app.route("/admin/rates/update", methods=["POST"])
@roles_required("manager")
def admin_rates_update():
    try:
        updated = {}
        for vt_key, vt in rate_manager.vehicle_types.items():
            tiers = []
            for i, (max_minutes, _fee) in enumerate(vt["tiers"]):
                fee_val = float(request.form.get(f"{vt_key}_fee_{i}", 0))
                if max_minutes is None:
                    tiers.append((None, fee_val))
                else:
                    minutes_val = int(request.form.get(f"{vt_key}_minutes_{i}", max_minutes))
                    tiers.append((minutes_val, fee_val))
            updated[vt_key] = {"label": vt["label"], "tiers": tiers}

        vip_surcharge = float(request.form.get("vip_surcharge", rate_manager.vip_surcharge))
        vat_rate_pct = float(request.form.get("vat_rate_pct", rate_manager.vat_rate * 100))
        vat_rate = round(vat_rate_pct / 100, 4)

        rate_manager.save(updated, vip_surcharge, vat_rate)
        flash("Rates updated — new fees apply immediately, no restart needed.", "success")
    except (ValueError, KeyError) as e:
        flash(f"Could not update rates: check that all fields are valid numbers. ({e})", "error")
    return redirect(url_for("admin_rates"))


# ---------------------------------------------------------------------
# Admin: slots (manager only)
# ---------------------------------------------------------------------
@app.route("/admin/slots")
@roles_required("manager")
def admin_slots():
    return render_template("admin_slots.html", slots=manager.get_slot_display())


@app.route("/admin/slots/toggle", methods=["POST"])
@roles_required("manager")
def admin_slots_toggle():
    slot_number = request.form.get("slot_number")
    action = request.form.get("action")
    ok, result = manager.set_slot_service_state(slot_number, in_service=(action == "activate"))
    if ok:
        flash(f"Slot {slot_number} is now {result}.", "success")
    else:
        flash(result, "error")
    return redirect(url_for("admin_slots"))


# ---------------------------------------------------------------------
# Admin: users (manager only)
# ---------------------------------------------------------------------
@app.route("/admin/users")
@roles_required("manager")
def admin_users():
    with get_conn() as conn:
        users = conn.execute("SELECT id, username, role FROM users ORDER BY id").fetchall()
    return render_template("admin_users.html", users=users)


@app.route("/admin/users/create", methods=["POST"])
@roles_required("manager")
def admin_users_create():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "attendant")

    if not username or not password:
        flash("Username and password are required.", "error")
    elif role not in ROLES:
        flash("Invalid role.", "error")
    else:
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), role),
                )
            flash(f"User {username} created.", "success")
        except Exception:
            flash("That username is already taken.", "error")
    return redirect(url_for("admin_users"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
