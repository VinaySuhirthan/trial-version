// auto_logout.js
(() => {
  // CONFIG
  const INACTIVITY_TIMEOUT_MS = 15 * 60 * 1000; // 15 minutes inactivity
  const WARNING_DURATION_MS = 60 * 1000; // show warning 60s before logout
  const HEALTH_POLL_INTERVAL_MS = 60 * 1000; // poll /health every minute if cookie not readable
  const LOGOUT_URL = "/logout";
  const HEALTH_URL = "/health";

  let inactivityTimer = null;
  let warningTimer = null;
  let logoutTimer = null;
  let countdownInterval = null;
  let remainingWarningMs = WARNING_DURATION_MS;

  // Helpers
  function getCookie(name) {
    const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return m ? decodeURIComponent(m.pop()) : null;
  }

  function base64UrlDecode(str) {
    try {
      str = str.replace(/-/g, '+').replace(/_/g, '/');
      const pad = str.length % 4;
      if (pad) str += '='.repeat(4 - pad);
      return decodeURIComponent(escape(window.atob(str)));
    } catch (e) {
      return null;
    }
  }

  function parseJwt(token) {
    if (!token) return null;
    const parts = token.split('.');
    if (parts.length < 2) return null;
    const payload = base64UrlDecode(parts[1]);
    if (!payload) return null;
    try { return JSON.parse(payload); } catch (e) { return null; }
  }

  function clearTimers() {
    clearTimeout(inactivityTimer);
    clearTimeout(warningTimer);
    clearTimeout(logoutTimer);
    clearInterval(countdownInterval);
    inactivityTimer = warningTimer = logoutTimer = countdownInterval = null;
  }

  function createWarningModal() {
    if (document.getElementById('auto-logout-modal')) return;
    const div = document.createElement('div');
    div.id = 'auto-logout-modal';
    div.style = `
      position:fixed; inset:0; display:flex; align-items:center; justify-content:center;
      background:rgba(2,6,23,0.6); z-index:9999;`;
    div.innerHTML = `
      <div style="width:380px; background:#0f172a; color:#e5e7eb; border-radius:10px; padding:18px; border:1px solid #1f2937; text-align:center;">
        <h3 style="margin:0 0 10px 0;">You're about to be signed out</h3>
        <p id="auto-logout-countdown" style="color:#9ca3af; margin:0 0 16px 0;">Signing out in 60s...</p>
        <div style="display:flex; gap:10px; justify-content:center;">
          <button id="auto-logout-stay" style="background:#10b981;color:white;border:none;padding:8px 14px;border-radius:6px;cursor:pointer;">Stay signed in</button>
          <button id="auto-logout-now" style="background:#ef4444;color:white;border:none;padding:8px 14px;border-radius:6px;cursor:pointer;">Sign out now</button>
        </div>
      </div>
    `;
    document.body.appendChild(div);

    document.getElementById('auto-logout-stay').onclick = () => {
      hideWarningModal();
      // Refresh by hitting a safe endpoint (reload will let server refresh cookies if your auth flow does)
      fetch(location.pathname, { method: 'GET', credentials: 'same-origin' }).then(() => {
        resetInactivityTimer(true);
      }).catch(() => { resetInactivityTimer(true); });
    };
    document.getElementById('auto-logout-now').onclick = () => doLogout();
  }

  function hideWarningModal() {
    const el = document.getElementById('auto-logout-modal');
    if (el) el.remove();
    clearInterval(countdownInterval);
  }

  function updateCountdownText(msLeft) {
    const el = document.getElementById('auto-logout-countdown');
    if (!el) return;
    const s = Math.max(0, Math.ceil(msLeft / 1000));
    el.textContent = `Signing out in ${s}s...`;
  }

  function showWarningModal(durationMs) {
    createWarningModal();
    remainingWarningMs = durationMs;
    updateCountdownText(remainingWarningMs);

    countdownInterval = setInterval(() => {
      remainingWarningMs -= 1000;
      if (remainingWarningMs <= 0) {
        clearInterval(countdownInterval);
      }
      updateCountdownText(remainingWarningMs);
    }, 1000);
  }

  function doLogout() {
    clearTimers();
    // call server logout endpoint which clears cookie server-side
    try { window.location.href = LOGOUT_URL; } catch (e) { window.location.href = '/login'; }
  }

  function scheduleLogoutByMs(totalMs) {
    clearTimers();
    if (totalMs <= 0) {
      return doLogout();
    }

    // If totalMs <= WARNING_DURATION_MS, show immediate warning
    const warnMs = Math.min(WARNING_DURATION_MS, totalMs);
    const timeUntilWarn = Math.max(0, totalMs - warnMs);

    warningTimer = setTimeout(() => {
      showWarningModal(warnMs);
    }, timeUntilWarn);

    logoutTimer = setTimeout(() => {
      doLogout();
    }, totalMs);
  }

  // Inactivity-based scheduling (resets on activity)
  function resetInactivityTimer(skipReschedule = false) {
    clearTimeout(inactivityTimer);
    clearTimeout(warningTimer);
    clearTimeout(logoutTimer);
    clearInterval(countdownInterval);

    // If we can read token expiry and it is earlier than inactivity window, prefer token expiry
    const token = getCookie('sb-access-token');
    const jwtPayload = token ? parseJwt(token) : null;
    if (jwtPayload && jwtPayload.exp) {
      const msLeft = jwtPayload.exp * 1000 - Date.now();
      if (msLeft > 0) {
        // schedule logout at token expiry or inactivity whichever is earlier
        const inactivityMs = INACTIVITY_TIMEOUT_MS;
        const chosen = Math.min(msLeft, inactivityMs);
        if (!skipReschedule) scheduleLogoutByMs(chosen);
        return;
      } else {
        // token expired
        doLogout();
        return;
      }
    }

    // If token is not accessible (HttpOnly cookie) we fallback to inactivity-only
    inactivityTimer = setTimeout(() => {
      // show warning then logout
      showWarningModal(WARNING_DURATION_MS);
      logoutTimer = setTimeout(doLogout, WARNING_DURATION_MS);
    }, INACTIVITY_TIMEOUT_MS);
  }

  // If cookie is HttpOnly we can't read it; poll /health to detect expiry
  let healthPollInterval = null;
  async function startHealthPollIfNeeded() {
    // If token readable, no poll required (we use token exp)
    if (parseJwt(getCookie('sb-access-token'))) return;
    // start poll if not running
    if (healthPollInterval) return;
    healthPollInterval = setInterval(async () => {
      try {
        const r = await fetch(HEALTH_URL, { method: 'GET', credentials: 'same-origin' });
        if (r.status === 401 || r.status === 403) {
          // server says not auth -> logout
          doLogout();
        }
      } catch (e) {
        // network error - ignore
      }
    }, HEALTH_POLL_INTERVAL_MS);
  }

  function stopHealthPoll() {
    if (healthPollInterval) clearInterval(healthPollInterval);
    healthPollInterval = null;
  }

  // Attach user-activity events (reset inactivity)
  const activityEvents = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'click'];
  function attachActivityListeners() {
    const resetFunc = () => resetInactivityTimer();
    activityEvents.forEach(ev => window.addEventListener(ev, resetFunc, { passive: true }));
  }

  // init
  function init() {
    // schedule initial timers
    resetInactivityTimer();
    attachActivityListeners();
    startHealthPollIfNeeded();

    // also re-check token every 30s in case it was refreshed
    setInterval(() => {
      const token = getCookie('sb-access-token');
      const payload = token ? parseJwt(token) : null;
      if (payload && payload.exp) {
        const msLeft = payload.exp * 1000 - Date.now();
        // if token expiry is sooner than current logoutTimer, reschedule
        if (logoutTimer) {
          // compute remaining
        }
        resetInactivityTimer(true);
      } else {
        // ensure health poll if token invisible
        startHealthPollIfNeeded();
      }
    }, 30 * 1000);
  }

  // Run when DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else init();

})();
