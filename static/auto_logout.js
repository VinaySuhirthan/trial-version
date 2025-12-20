// static/auto_logout.js
(function () {
  // CONFIG
  // Set inactivity timeout (ms). Use 120000 for 2 min, 30000 for 30s while testing.
  const INACTIVITY_MS = 120000; // <-- change this to 30000 to test quickly
  const SHOW_COUNTDOWN_LAST_MS = 30000; // show banner only during last 30s
  const COOKIE_NAME = "sb-access-token"; // token cookie to check (adjust if different)
  const LOG = true;

  // Utility: read cookie
  function readCookie(name) {
    const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return v ? decodeURIComponent(v.pop()) : null;
  }

  // If no auth cookie, do nothing (do not redirect)
  const token = readCookie(COOKIE_NAME);
  if (!token) {
    if (LOG) console.log('[auto_logout] no token cookie found — auto-logout disabled');
    return;
  }

  // Create countdown banner (hidden initially)
  const banner = document.createElement('div');
  banner.id = 'auto-logout-banner';
  banner.style.cssText = [
    'position:fixed',
    'bottom:20px',
    'right:20px',
    'z-index:99999',
    'background:rgba(0,0,0,0.8)',
    'color:white',
    'padding:10px 14px',
    'border-radius:8px',
    'font-family:Inter,system-ui,Segoe UI,Roboto,sans-serif',
    'font-size:14px',
    'display:none',
    'align-items:center',
    'gap:10px',
  ].join(';');
  banner.innerHTML = `<span id="auto-logout-icon">⏳</span> <span id="auto-logout-text">Auto logout in 30s</span> <button id="auto-logout-stay" style="margin-left:8px;background:#2563eb;border:none;color:#fff;padding:6px 8px;border-radius:6px;cursor:pointer">Stay</button>`;
  document.body.appendChild(banner);

  const textEl = document.getElementById('auto-logout-text');
  const stayBtn = document.getElementById('auto-logout-stay');

  // Timer state
  let lastActivity = Date.now();
  let countdownTimer = null;
  let bannerVisible = false;

  function resetTimer() {
    lastActivity = Date.now();
    if (bannerVisible) hideBanner();
    if (LOG) console.log('[auto_logout] timer reset at', new Date(lastActivity).toISOString());
  }

  function showBanner(remainingMs) {
    bannerVisible = true;
    banner.style.display = 'flex';
    updateBanner(remainingMs);
  }

  function hideBanner() {
    bannerVisible = false;
    banner.style.display = 'none';
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
  }

  function updateBanner(remainingMs) {
    const s = Math.max(0, Math.ceil(remainingMs / 1000));
    textEl.textContent = `Auto logout in ${s}s`;
  }

  function performLogout() {
    if (LOG) console.log('[auto_logout] performing logout redirect');
    // Clear cookies client-side (best-effort)
    document.cookie = `${COOKIE_NAME}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
    // add any other cookie names you use:
    document.cookie = `sb-refresh-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;

    // Redirect to login
    window.location.href = '/login';
  }

  // main loop: checks inactivity
  function tick() {
    const now = Date.now();
    const elapsed = now - lastActivity;
    const remaining = INACTIVITY_MS - elapsed;

    if (remaining <= 0) {
      // expired
      if (LOG) console.log('[auto_logout] inactivity expired');
      performLogout();
      return;
    }

    if (remaining <= SHOW_COUNTDOWN_LAST_MS) {
      if (!bannerVisible) {
        showBanner(remaining);
        // start countdown updates
        countdownTimer = setInterval(() => {
          const now2 = Date.now();
          const rem2 = INACTIVITY_MS - (now2 - lastActivity);
          if (rem2 <= 0) {
            clearInterval(countdownTimer);
            countdownTimer = null;
            performLogout();
          } else updateBanner(rem2);
        }, 500);
      } else {
        updateBanner(remaining);
      }
    }
  }

  // Attach activity listeners to reset timer
  ['click', 'keydown', 'mousemove', 'scroll', 'touchstart'].forEach(evt => {
    document.addEventListener(evt, resetTimer, { passive: true });
  });

  // "Stay" button to reset
  stayBtn.addEventListener('click', (e) => {
    e.preventDefault();
    resetTimer();
  });

  // Start periodic check
  const interval = setInterval(tick, 1000);

  // Start with a console message
  if (LOG) console.log(`[auto_logout] started: timeout=${INACTIVITY_MS}ms, countdown_shows_last=${SHOW_COUNTDOWN_LAST_MS}ms`);
})();
