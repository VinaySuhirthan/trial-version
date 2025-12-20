// /static/auto_logout.js
(function () {
  const TIMER_ID = 'auto-logout-timer';
  const STORAGE_KEY = 'auto_logout_expiry_v1';
  const DEFAULT_TIMEOUT = 120; // seconds (change via data-timeout on element)

  const el = document.getElementById(TIMER_ID);
  if (!el) {
    console.warn('auto_logout: timer element not found:', TIMER_ID);
    return;
  }

  // read configured timeout from the element attribute, fallback to default
  const configured = parseInt(el.dataset.timeout, 10);
  const TIMEOUT = Number.isFinite(configured) && configured > 0 ? configured : DEFAULT_TIMEOUT;

  // Helper: format seconds -> M:SS
  function fmt(s) {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  // Set expiry timestamp (ms) to localStorage
  function setExpiryTs(tsMs) {
    try { localStorage.setItem(STORAGE_KEY, String(tsMs)); } catch (e) {}
  }
  function getExpiryTs() {
    try { return parseInt(localStorage.getItem(STORAGE_KEY) || '0', 10); } catch (e) { return 0; }
  }

  // Initialise expiry if missing
  if (!getExpiryTs()) {
    setExpiryTs(Date.now() + TIMEOUT * 1000);
  }

  // Update UI
  function updateUI(remainingSec) {
    if (remainingSec <= 0) {
      el.textContent = '⏳ Auto logout in 0:00';
      el.style.opacity = '0.9';
      el.style.background = '#330000';
      el.style.color = '#ffdddd';
      return;
    }
    el.textContent = `⏳ Auto logout in ${fmt(remainingSec)}`;
    // small visual change on warning
    if (remainingSec <= 10) {
      el.style.background = '#2a1111';
      el.style.color = '#ffd6d6';
      el.style.boxShadow = '0 0 8px rgba(255, 0, 0, 0.12)';
    } else {
      el.style.background = '';
      el.style.color = '';
      el.style.boxShadow = '';
    }
  }

  // Logout fallback (uses your logout() if present)
  function performLogout() {
    try {
      if (typeof logout === 'function') {
        logout();
        return;
      }
    } catch (e) {}
    // fallback: clear common supabase cookies and redirect
    document.cookie = "sb-access-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
    document.cookie = "sb-refresh-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
    // final redirect
    window.location.href = '/login';
  }

  // Activity handler: reset expiry
  let lastActivity = Date.now();
  function onActivity() {
    lastActivity = Date.now();
    const newExpiry = Date.now() + TIMEOUT * 1000;
    setExpiryTs(newExpiry);
    updateUI(TIMEOUT);
  }

  // Attach activity listeners
  const activityEvents = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll'];
  activityEvents.forEach(ev => window.addEventListener(ev, throttle(onActivity, 1000), { passive: true }));

  // Throttle helper
  function throttle(fn, wait) {
    let last = 0;
    return function (...args) {
      const now = Date.now();
      if (now - last >= wait) {
        last = now;
        fn.apply(this, args);
      }
    };
  }

  // Sync across tabs: when storage changes update UI
  window.addEventListener('storage', (ev) => {
    if (ev.key === STORAGE_KEY) {
      // another tab updated expiry
      const expiry = getExpiryTs();
      const remaining = Math.max(0, Math.ceil((expiry - Date.now()) / 1000));
      updateUI(remaining);
    }
  });

  // Visibility change: update immediately when tab visible
  document.addEventListener('visibilitychange', () => {
    const expiry = getExpiryTs();
    const remaining = Math.max(0, Math.ceil((expiry - Date.now()) / 1000));
    updateUI(remaining);
  });

  // Main timer loop (1s)
  const interval = setInterval(() => {
    const expiry = getExpiryTs();
    const now = Date.now();
    const remainingSec = Math.max(0, Math.ceil((expiry - now) / 1000));
    updateUI(remainingSec);

    if (remainingSec <= 0) {
      clearInterval(interval);
      setTimeout(performLogout, 300); // give UI a moment
    }
  }, 1000);

  // Initial render
  (function init() {
    const expiry = getExpiryTs();
    const remainingSec = Math.max(0, Math.ceil((expiry - Date.now()) / 1000));
    updateUI(remainingSec);
  })();

})();
