document.addEventListener("DOMContentLoaded", function () {
  const TIMER_ID = "auto-logout-timer";
  const STORAGE_KEY = "auto_logout_expiry";
  const DEFAULT_TIMEOUT = 120; // seconds (2 minutes)

  const timerEl = document.getElementById(TIMER_ID);
  if (!timerEl) {
    console.warn("auto_logout: timer element not found:", TIMER_ID);
    return;
  }

  const configured = parseInt(timerEl.dataset.timeout, 10);
  const TIMEOUT = Number.isFinite(configured) && configured > 0
    ? configured
    : DEFAULT_TIMEOUT;

  function format(sec) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  function setExpiry(ts) {
    localStorage.setItem(STORAGE_KEY, String(ts));
  }

  function getExpiry() {
    return parseInt(localStorage.getItem(STORAGE_KEY) || "0", 10);
  }

  function resetTimer() {
    const expiry = Date.now() + TIMEOUT * 1000;
    setExpiry(expiry);
    updateUI(TIMEOUT);
  }

  function updateUI(remaining) {
    timerEl.textContent = `⏳ Auto logout in ${format(remaining)}`;
  }

  function logout() {
    document.cookie = "sb-access-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
    document.cookie = "sb-refresh-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
    window.location.href = "/login";
  }

  if (!getExpiry()) {
    resetTimer();
  }

  const activityEvents = ["mousemove", "mousedown", "keydown", "scroll", "touchstart"];
  activityEvents.forEach(evt =>
    document.addEventListener(evt, resetTimer, { passive: true })
  );

  setInterval(() => {
    const expiry = getExpiry();
    const remaining = Math.ceil((expiry - Date.now()) / 1000);

    if (remaining <= 0) {
      updateUI(0);
      logout();
    } else {
      updateUI(remaining);
    }
  }, 1000);
});
