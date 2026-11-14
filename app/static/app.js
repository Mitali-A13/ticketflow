// Two small jobs: show times in the visitor's timezone, and refresh a page
// region while a job is still in progress. No framework.
(function () {
  "use strict";

  function formatTimes(root) {
    root.querySelectorAll("time[data-epoch]").forEach(function (el) {
      var d = new Date(Number(el.dataset.epoch) * 1000);
      if (isNaN(d.getTime())) return;
      el.textContent = d.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      el.title = d.toLocaleString();
    });
  }
  formatTimes(document);

  var root = document.getElementById("live");
  if (!root || root.dataset.live !== "on") return;

  var timer = null;
  var busy = false;

  function stop() { if (timer) clearInterval(timer); }

  function tick() {
    if (busy || document.hidden) return;
    busy = true;
    fetch(location.href, { credentials: "same-origin", headers: { Accept: "text/html" } })
      .then(function (r) {
        if (r.redirected) { stop(); throw new Error("signed out"); }
        if (r.status === 429) throw new Error("rate limited, skip this round");
        if (!r.ok) { stop(); throw new Error("stopped"); }
        return r.text();
      })
      .then(function (html) {
        var fresh = new DOMParser().parseFromString(html, "text/html").getElementById("live");
        if (!fresh) { stop(); return; }
        root.innerHTML = fresh.innerHTML;
        formatTimes(root);
        if (fresh.dataset.live !== "on") stop();
      })
      .catch(function () { /* try again next round, unless stopped above */ })
      .finally(function () { busy = false; });
  }

  timer = setInterval(tick, Number(root.dataset.interval) || 3000);
})();