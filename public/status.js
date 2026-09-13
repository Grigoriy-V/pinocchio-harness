// The status card: a panel fastened above the composer, opened and closed
// by a button inside the composer, kept current while it is open. Reads
// `/status` (this app's own route, `ui/chainlit_app.py`) and draws in
// place, so nothing flickers.
(function () {
  const POLL_MS = 3000;
  let open = false;
  let timer = null;

  const card = document.createElement("div");
  card.id = "status-card";
  card.hidden = true;
  document.body.appendChild(card);

  const button = document.createElement("button");
  button.id = "status-button";
  button.type = "button";
  button.title = "Status";
  button.textContent = "Status";
  button.addEventListener("click", () => toggle());

  // The composer is Chainlit's and remounts on navigation; the button is
  // put back whenever it is missing, and the card follows the composer.
  function fasten() {
    const composer = document.getElementById("message-composer");
    if (!composer) return;
    if (button.parentElement !== composer) {
      composer.appendChild(button);
    }
    if (open) {
      const rect = composer.getBoundingClientRect();
      card.style.left = rect.left + "px";
      card.style.width = rect.width + "px";
      card.style.bottom = window.innerHeight - rect.top + 8 + "px";
    }
  }
  new MutationObserver(fasten).observe(document.body, { childList: true, subtree: true });
  window.addEventListener("resize", fasten);
  fasten();

  function toggle(force) {
    open = force === undefined ? !open : force;
    card.hidden = !open;
    button.classList.toggle("open", open);
    if (open) {
      fasten();
      refresh();
      timer = setInterval(refresh, POLL_MS);
    } else if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  const fmt = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US"));
  const short = (n) => (n >= 1000 ? Math.round(n / 1000) + "K" : String(n));
  const money = (n) => (n === null || n === undefined ? "—" : "$" + Number(n).toFixed(n < 1 ? 4 : 2));

  function bar(share) {
    const width = Math.max(0, Math.min(100, Math.round(share * 100)));
    return '<span class="bar"><span class="fill" style="width:' + width + '%"></span></span>';
  }

  function row(label, value) {
    return '<div class="row"><span class="label">' + label + '</span><span class="value">' + value + "</span></div>";
  }

  function draw(s) {
    let context;
    if (s.context_window) {
      const share = s.context_used / s.context_window;
      context =
        bar(share) + " " +
        (s.context_estimated ? "~" : "") + fmt(s.context_used) + " of " + short(s.context_window) +
        " (" + Math.round(share * 100) + "%)" +
        (s.last_cached ? ", " + fmt(s.last_cached) + " cached" : "");
    } else {
      context = (s.context_estimated ? "~" : "") + fmt(s.context_used) + " tokens";
    }
    const session =
      s.session_spend === null
        ? "—"
        : money(s.session_spend) + (s.session_spend_exact ? " for this session's calls" : " on the account since start");
    let account;
    if (s.credits_total !== null) {
      const left = s.credits_total - s.credits_used;
      account = bar(s.credits_total ? left / s.credits_total : 0) + " " + money(left) + " left of " + money(s.credits_total);
    } else {
      account = "not an OpenRouter set";
    }
    card.innerHTML =
      '<div class="head"><span>Status</span><button type="button" id="status-close">Close</button></div>' +
      row("Thread", "<code>" + s.thread_id + "</code>") +
      row("Model", s.model_set + " · " + s.model_name) +
      row("Context", context) +
      row("Session", session) +
      row("Account", account) +
      row("Switches", "mode " + s.mode + " · plan " + (s.plan ? "on" : "off") + " · size " + s.context_size) +
      row("Folder", "<code>" + s.workspace + "</code>");
    document.getElementById("status-close").addEventListener("click", () => toggle(false));
  }

  async function refresh() {
    try {
      const response = await fetch("/status", { cache: "no-store" });
      if (!response.ok) {
        card.innerHTML = '<div class="head"><span>Status</span></div><div class="row">no session yet</div>';
        return;
      }
      draw(await response.json());
    } catch (error) {
      card.innerHTML = '<div class="head"><span>Status</span></div><div class="row">unreachable</div>';
    }
  }
})();
