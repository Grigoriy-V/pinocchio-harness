// The status card: a small panel above the composer, opened and closed by
// one button, kept current while it is open. Reads `/status` (this app's
// own route, `ui/chainlit_app.py`) and draws in place, so nothing flickers.
(function () {
  const POLL_MS = 3000;
  let open = false;
  let timer = null;

  const card = document.createElement("div");
  card.id = "status-card";
  card.hidden = true;
  const button = document.createElement("button");
  button.id = "status-button";
  button.type = "button";
  button.textContent = "Status";
  button.addEventListener("click", () => toggle());
  document.body.appendChild(card);
  document.body.appendChild(button);

  function toggle(force) {
    open = force === undefined ? !open : force;
    card.hidden = !open;
    button.classList.toggle("open", open);
    if (open) {
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
    const cells = 40;
    const filled = Math.max(0, Math.min(cells, Math.round(share * cells)));
    return (
      '<span class="bar"><span class="fill" style="width:' + (filled / cells) * 100 + '%"></span></span>'
    );
  }

  function row(label, value) {
    return '<div class="row"><span class="label">' + label + '</span><span class="value">' + value + "</span></div>";
  }

  function draw(s) {
    let context;
    if (s.context_used !== null && s.context_budget) {
      const share = s.context_used / s.context_budget;
      context =
        bar(share) +
        " Remaining " + Math.max(0, Math.round((1 - share) * 100)) + "%  " +
        "Used " + fmt(s.context_used) + " of " + short(s.context_budget) +
        (s.last_cached ? " (" + fmt(s.last_cached) + " cached)" : "");
    } else if (s.context_used !== null) {
      context = "Used " + fmt(s.context_used) + " in the last request";
    } else {
      context = "~" + fmt(s.context_estimate) + " estimated for the next request";
    }
    let account;
    if (s.credits_total !== null) {
      const left = s.credits_total - s.credits_used;
      account =
        bar(s.credits_total ? left / s.credits_total : 0) +
        " " + money(left) + " left of " + money(s.credits_total);
    } else {
      account = "not an OpenRouter set";
    }
    card.innerHTML =
      '<div class="head"><span>Status</span><button type="button" id="status-close">Close</button></div>' +
      row("Thread", "<code>" + s.thread_id + "</code>") +
      row("Model", s.model_set + " · " + s.model_name) +
      row("Context", context) +
      row("History", s.messages + " messages verbatim" + (s.summarized_through ? ", " + s.summarized_through + " in the summary" : "")) +
      row("Session", s.session_spend === null ? "—" : money(s.session_spend) + " spent since start") +
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
