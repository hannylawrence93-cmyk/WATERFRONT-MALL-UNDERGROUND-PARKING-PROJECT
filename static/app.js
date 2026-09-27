// app.js
// Polls /api/slots every few seconds and updates the summary counts +
// slot colors/plates in place, so the dashboard feels "live" (e.g. on
// a kiosk screen at the gate) without a full page reload.

const POLL_MS = 8000;

async function refreshSlots() {
    try {
        const res = await fetch("/api/slots");
        if (!res.ok) return;
        const data = await res.json();

        const availEl = document.getElementById("sumAvailable");
        const occEl = document.getElementById("sumOccupied");
        if (availEl) availEl.textContent = data.available;
        if (occEl) occEl.textContent = data.occupied;

        data.slots.forEach((s) => {
            const el = document.querySelector(`.slot[data-slot="${s.slot_number}"]`);
            if (!el) return;
            el.classList.remove("available", "occupied", "out_of_service");
            el.classList.add(s.status);

            let small = el.querySelector("small");
            if (s.plate_number) {
                if (!small) {
                    small = document.createElement("small");
                    el.appendChild(small);
                }
                small.textContent = s.plate_number;
            } else if (small) {
                small.remove();
            }
        });
    } catch (e) {
        // silent fail: the page still works, just without live refresh
        console.warn("slot refresh failed", e);
    }
}

if (document.querySelector(".slot-grid")) {
    setInterval(refreshSlots, POLL_MS);
}
