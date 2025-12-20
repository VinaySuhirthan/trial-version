(() => {
  const TOTAL_TIME = 2 * 60 * 1000; // 2 minutes
  const WARNING_AT = 30 * 1000;     // show countdown in last 30s

  let timer, countdownInterval, startTime;

  function createBanner() {
    const div = document.createElement("div");
    div.id = "auto-logout-banner";
    div.style.cssText = `
      position: fixed;
      bottom: 20px;
      right: 20px;
      background: #020617;
      color: #facc15;
      padding: 12px 16px;
      border-radius: 10px;
      border: 1px solid #facc15;
      font-family: Inter, sans-serif;
      font-size: 14px;
      z-index: 9999;
      display: none;
      box-shadow: 0 10px 25px rgba(0,0,0,0.4);
    `;
    document.body.appendChild(div);
    return div;
  }

  const banner = createBanner();

  function logout() {
    document.cookie = "sb-access-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
    document.cookie = "sb-refresh-token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
    window.location.href = "/login";
  }

  function startTimer() {
    clearTimeout(timer);
    clearInterval(countdownInterval);
    banner.style.display = "none";

    startTime = Date.now();

    timer = setTimeout(logout, TOTAL_TIME);

    setTimeout(() => {
      banner.style.display = "block";

      countdownInterval = setInterval(() => {
        const elapsed = Date.now() - startTime;
        const remaining = Math.max(0, TOTAL_TIME - elapsed);
        const sec = Math.ceil(remaining / 1000);

        banner.textContent = `⏳ Auto logout in ${sec}s`;
      }, 1000);
    }, TOTAL_TIME - WARNING_AT);
  }

  ["click", "keydown", "mousemove", "scroll"].forEach(evt =>
    document.addEventListener(evt, startTimer)
  );

  startTimer();
})();
