// static/auto_logout.js
(function () {
  function start() {
    const TOTAL_MS = 2 * 60 * 1000; // 2 minutes
    let remaining = TOTAL_MS;
    let interval, timeout;

    const box = document.createElement("div");
    box.id = "auto-logout-timer";
    box.style.cssText = `
      position: fixed;
      top: 20px;
      right: 20px;
      background: #020617;
      color: #22c55e;
      padding: 10px 14px;
      border-radius: 10px;
      font-family: Inter, system-ui, Arial;
      font-size: 14px;
      z-index: 999999;
      border: 1px solid #22c55e;
      box-shadow: 0 10px 25px rgba(0,0,0,0.4);
    `;
    document.body.appendChild(box);

    function render() {
      const sec = Math.ceil(remaining / 1000);
      box.textContent = `⏳ Auto logout in ${sec}s`;
    }

    function logout() {
      document.cookie = "sb-access-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
      document.cookie = "sb-refresh-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
      window.location.href = "/login";
    }

    function reset() {
      clearInterval(interval);
      clearTimeout(timeout);

      remaining = TOTAL_MS;
      render();

      interval = setInterval(() => {
        remaining -= 1000;
        render();
      }, 1000);

      timeout = setTimeout(logout, TOTAL_MS);
    }

    ["click", "keydown", "mousemove", "scroll"].forEach(evt =>
      document.addEventListener(evt, reset)
    );

    reset();
  }

  // 🔑 THIS IS THE FIX
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
