// static/auto_logout.js
// Strict auto-logout: sets expiry once and never resets on user activity.
// Place <div id="auto-logout-timer" data-timeout="180"> in your HTML.

document.addEventListener("DOMContentLoaded", () => {
  const TIMER_ID = "auto-logout-timer";
  const STORAGE_KEY = "auto_logout_expiry_strict";
  const DEFAULT_TIMEOUT = 180; // seconds (3 minutes) - strict by default

  const el = document.getElementById(TIMER_ID);
  if (!el) {
    console.warn("auto_logout (strict): timer element not found:", TIMER_ID);
    return;
  }

  // Read configured timeout (seconds) from element attribute, fallback to default
  const configured = parseInt(el.dataset.timeout, 10);
  const TIMEOUT = Number.isFinite(configured) && configured > 0 ? configured : DEFAULT_TIMEOUT;

  function formatSeconds(sec) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  function setExpiry(timestampMs) {
    try {
      localStorage.setItem(STORAGE_KEY, String(timestampMs));
    } catch (e) {
      console.warn("auto_logout: failed to write expiry to localStorage", e);
    }
  }

  function getExpiry() {
    const v = localStorage.getItem(STORAGE_KEY);
    return v ? parseInt(v, 10) : 0;
  }

  function updateUI(remainingSec) {
    el.textContent = `⏳ Auto logout in ${formatSeconds(Math.max(0, remainingSec))}`;
  }

  function logoutNow() {
    // wipe auth cookies (adjust names if different)
    document.cookie = "sb-access-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
    document.cookie = "sb-refresh-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
    // redirect to login
    window.location.href = "/login";
  }

  // If no expiry present, set it ONCE to now + TIMEOUT.
  let expiry = getExpiry();
  if (!expiry) {
    expiry = Date.now() + TIMEOUT * 1000;
    setExpiry(expiry);
  }

  // If already expired, logout immediately.
  if (expiry <= Date.now()) {
    updateUI(0);
    logoutNow();
    return;
  }

  // Tick every second and update UI. Do NOT reset on user activity.
  const tick = () => {
    expiry = getExpiry(); // read current stored expiry (keeps tabs in sync)
    const remaining = Math.ceil((expiry - Date.now()) / 1000);

    if (remaining <= 0) {
      updateUI(0);
      logoutNow();
      return;
    }

    updateUI(remaining);
  };

  // Start interval
  tick();
  const intervalId = setInterval(tick, 1000);

  // Keep in sync with other tabs: respond to storage changes
  window.addEventListener("storage", (ev) => {
    if (ev.key === STORAGE_KEY) {
      const newExpiry = parseInt(ev.newValue || "0", 10);
      if (!newExpiry || newExpiry <= Date.now()) {
        // expired or removed in another tab
        updateUI(0);
        clearInterval(intervalId);
        logoutNow();
      } else {
        // update UI immediately based on new expiry
        expiry = newExpiry;
        tick();
      }
    }
  }, false);

  // Expose small helper (optional) to force-set expiry from console if you need it.
  // Use only for debugging; do NOT call automatically.
  window.__autoLogoutStrict = {
    getExpiry: () => getExpiry(),
    setExpiryToNowPlus: (secs) => { setExpiry(Date.now() + Number(secs || TIMEOUT) * 1000); }
  };
});
