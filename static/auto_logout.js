(() => {
  const TOTAL_MS = 2 * 60 * 1000; // 2 minutes
  let remaining = TOTAL_MS;
  let interval, timeout;

  const box = document.createElement("div");
  box.style.cssText = `
    position:fixed;
    bottom:20px;
    right:20px;
    background:#020617;
    color:#facc15;
    padding:10px 14px;
    border-radius:10px;
    font-family:Inter,system-ui;
    font-size:14px;
    z-index:9999;
    border:1px solid #facc15;
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

  ["click", "keydown", "mousemove", "scroll"].forEach(e =>
    document.addEventListener(e, reset)
  );

  reset();
})();
