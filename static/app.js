const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => (n == null ? "" : `$${Number(n).toFixed(0)}`);
const tok = (n) => Number(n || 0).toLocaleString();
const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
                set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };

const form = $("#find"), msg = $("#message"), go = $("#go"), statusEl = $("#status"), results = $("#results");
const STEPS = [["Guard", "Checking your message"], ["Profiler", "Reading who's playing"], ["Sniper", "Scanning tee sheets"],
  ["Analyst", "Scoring every course"], ["Concierge", "Picking your best options"]];
const NEED_LABELS = {
  walking_ability: { limited: "Limited walking", minimal: "Minimal walking" },
  terrain: { avoid_steep: "No steep hills", flat_only: "Flat only" },
  forced_carries: { avoid_long: "No long carries", avoid_all: "No forced carries" },
  bunkers: { avoid_deep: "No deep bunkers" },
  cart_near_greens: { true: "Cart near greens" }, adaptive_cart: { true: "Adaptive cart" }, shorter_course: { true: "Shorter course" },
};
const VERDICT = { great_fit: "Great fit", workable: "Workable", poor_fit: "Poor fit" };
const TERRAIN = { flat: "Flat", gently_rolling: "Gently rolling", hilly: "Hilly", severe: "Very hilly" };
const WINDOW = { early: "Early (before 8)", morning: "Morning", midday: "Midday", afternoon: "Afternoon", twilight: "Twilight", any: "Any time" };
let lastPlayers = 1;

/* ---------- Reading options: text size + high contrast ---------- */
function setSize(n) {
  document.documentElement.dataset.size = n;
  $$(".size-switch button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.size === String(n))));
  store.set("cs-size", n);
}
$$(".size-switch button").forEach((b) => b.addEventListener("click", () => setSize(b.dataset.size)));
setSize(document.documentElement.dataset.size || "1");

const contrastBtn = $("#contrast");
function setContrast(on) {
  if (on) document.documentElement.dataset.contrast = "high"; else delete document.documentElement.dataset.contrast;
  contrastBtn.setAttribute("aria-pressed", String(on));
  store.set("cs-contrast", on ? "high" : "");
}
contrastBtn.addEventListener("click", () => setContrast(contrastBtn.getAttribute("aria-pressed") !== "true"));
setContrast(document.documentElement.dataset.contrast === "high");

/* ---------- Voice input (only where the browser supports it) ---------- */
const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
if (Speech) {
  const mic = $("#mic"); mic.hidden = false;
  let rec = null;
  mic.addEventListener("click", () => {
    if (rec) { rec.stop(); return; }
    rec = new Speech(); rec.lang = "en-US"; rec.interimResults = true;
    const before = msg.value.trim();
    rec.onresult = (e) => { msg.value = (before ? before + " " : "") + [...e.results].map((r) => r[0].transcript).join(""); };
    rec.onend = () => { rec = null; mic.classList.remove("listening"); mic.setAttribute("aria-label", "Speak your request instead of typing"); };
    rec.start(); mic.classList.add("listening"); mic.setAttribute("aria-label", "Stop listening");
  });
}

/* ---------- Examples: hero starters and "Who it's for" tiles ---------- */
$$("[data-example]").forEach((b) => b.addEventListener("click", () => {
  msg.value = b.dataset.example;
  if (b.classList.contains("tile")) $("#find").scrollIntoView({ behavior: "smooth", block: "center" });
  msg.focus({ preventScroll: true });
}));

fetch("/api/health").then((r) => r.json()).then((h) => { $("#model-line").textContent = `Model: ${h.model}`; }).catch(() => {});

/* ---------- Search ---------- */
let timer;
function showProgress() {
  statusEl.hidden = false; statusEl.className = "status-band";
  statusEl.innerHTML = `<div class="wrap"><p class="kicker">Working on it</p><ol class="progress">${STEPS.map(([a, b]) => `<li><b>${a}</b>${b}</li>`).join("")}</ol></div>`;
  const items = $$("li", statusEl);
  let i = 0;
  const tick = () => { items.forEach((li, j) => li.className = j < i ? "done" : j === i ? "on" : ""); if (i < items.length - 1) i++; };
  tick(); timer = setInterval(tick, 1800);
  statusEl.scrollIntoView({ behavior: "smooth", block: "center" });
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = msg.value.trim();
  if (!message) { msg.focus(); return; }
  go.disabled = true; go.firstChild.textContent = "Searching… "; results.hidden = true; showProgress();
  try {
    const r = await fetch("/api/search", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "Search failed.");
    statusEl.hidden = true; render(data);
  } catch (err) {
    statusEl.className = "status-band status-error";
    statusEl.innerHTML = `<div class="wrap" role="alert"><p class="kicker">Couldn't finish that search</p><p>${esc(err.message)}</p></div>`;
  } finally {
    clearInterval(timer); go.disabled = false; go.firstChild.textContent = "Find my tee time ";
  }
});

function needChips(needs) {
  return Object.entries(needs).map(([k, v]) => NEED_LABELS[k]?.[String(v)]).filter(Boolean).map((t) => `<li class="chip chip-need">${esc(t)}</li>`).join("");
}

function recCard(r, i) {
  const fx = { helps: "✓", hurts: "!", neutral: "•" };
  const findings = r.findings.map((f) => `<li><span class="fx ${f.effect}" aria-label="${f.effect}">${fx[f.effect]}</span><span>${esc(f.finding)}</span></li>`).join("");
  const verify = r.verify_with_course.length ? `<p class="verify"><b>Call to confirm:</b> ${r.verify_with_course.map(esc).join(" · ")}</p>` : "";
  return `<article class="rec ${i === 0 ? "rec-best" : ""}" aria-label="${esc(r.course_name)}, ${esc(r.tee_time)}">
    <div class="rec-main">
      ${i === 0 ? '<span class="rec-flag">Best match</span>' : ""}
      <h3>${esc(r.course_name)}</h3>
      <p class="rec-town">${esc(r.town || "")}</p>
      <dl class="rec-facts">
        <div><dt>Tee time</dt><dd>${esc(r.tee_time)}</dd></div>
        <div><dt>Per player</dt><dd>${money(r.price_per_player)} ${r.deal ? '<span class="deal">Deal</span>' : ""}</dd></div>
        <div><dt>Open spots</dt><dd>${r.open_spots}</dd></div>
      </dl>
      <p class="why">${esc(r.why_it_fits)}</p>
      ${r.heads_up ? `<p class="heads"><b>Heads up:</b> ${esc(r.heads_up)}</p>` : ""}
      <ul class="findings">${findings}</ul>${verify}
      <div class="rec-actions">
        ${r.phone ? `<a class="btn btn-green" href="tel:${esc(r.phone)}">Call ${esc(r.phone)}</a>` : ""}
        ${r.website ? `<a class="btn btn-outline" href="${esc(r.website)}" target="_blank" rel="noopener">Course website <span aria-hidden="true">↗</span></a>` : ""}
      </div>
    </div>
    <div class="rec-side">
      <div class="ring" style="--p:${r.accessibility_match_score}" role="img" aria-label="Accessibility match ${r.accessibility_match_score} out of 100"><span>${r.accessibility_match_score}</span></div>
      <div><small>Accessibility match</small><br><span class="verdict">${VERDICT[r.verdict]}</span></div>
    </div>
  </article>`;
}

function render(d) {
  lastPlayers = d.searched.players;
  const s = d.searched, u = d.usage;
  const facts = d.safety.filter((x) => x.layer === "code: fact check");
  const main = d.safety.filter((x) => x.layer !== "code: fact check");
  const item = (x) => `<li><span class="layer">${esc(x.layer)}</span>: ${esc(x.event)}</li>`;
  const shield = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 4 5v6c0 5 3.4 9.3 8 11 4.6-1.7 8-6 8-11V5z" fill="currentColor"/></svg>';
  const safety = d.safety.length ? `<div class="safety" ${main.length ? 'role="alert"' : ""}>
      <h3>${shield} ${main.length ? `Safety nets caught ${main.length} thing${main.length === 1 ? "" : "s"}` : "Fact-checked"}</h3>
      ${main.length ? `<ul>${main.map(item).join("")}</ul>` : ""}
      ${facts.length ? `<details><summary>${facts.length} AI claim${facts.length === 1 ? "" : "s"} about facts the course data doesn't have were removed, or the score was capped</summary><ul>${facts.map(item).join("")}</ul></details>` : ""}</div>` : "";
  const waitlist = d.waitlist_options.length ? `<div class="sub"><h3>Full right now, waitlist open</h3><div class="rows">
      ${d.waitlist_options.map((w) => `<div class="row"><span><b>${esc(w.course_name)}</b> · ${esc(w.tee_time)} · ${money(w.price_per_player)} <span class="muted">· match ${w.score}</span></span>
        <button class="btn btn-outline" data-slot="${esc(w.slot_id)}">Join waitlist</button></div>`).join("")}</div></div>` : "";
  const others = d.other_courses.length ? `<div class="sub"><h3>Other courses we checked</h3><div class="rows">
      ${d.other_courses.map((o) => `<div class="row"><span><b>${esc(o.course_name)}</b> <span class="muted">· ${esc(o.one_line)}</span></span>
        <span class="badge ${o.verdict}">${VERDICT[o.verdict]} · ${o.score}</span></div>`).join("")}</div></div>` : "";
  const trace = d.trace.map((t) => `<li><span class="n">${t.step}</span><div>
      <div><span class="tag ${t.kind === "ai" ? "tag-ai" : "tag-code"}">${t.kind === "ai" ? "AI" : "Code"}</span><span class="who">${esc(t.who)}</span>
      <span class="meta"> · ${t.ms} ms${t.usage && !Array.isArray(t.usage) ? ` · ${tok(t.usage.input_tokens)} in (${tok(t.usage.cached_input_tokens)} cached) / ${tok(t.usage.output_tokens)} out` : ""}</span></div>
      <div>${esc(t.summary)}</div>
      ${t.output || t.scores ? `<pre>${esc(JSON.stringify(t.output || { scores: t.scores, from_cache: t.cache_hits, fresh: t.fresh }, null, 2))}</pre>` : ""}
    </div></li>`).join("");
  const calls = u.calls.map((c) => `<tr><td>${esc(c.agent)}${c.label ? ` · ${esc(c.label)}` : ""}</td><td>${tok(c.input_tokens)}</td><td>${tok(c.cached_input_tokens)}</td><td>${tok(c.output_tokens)}</td><td>$${c.cost_usd.toFixed(5)}</td></tr>`).join("");

  results.innerHTML = `<div class="wrap">
    <div class="result-head"><div>
      <p class="kicker kicker-dark">Your round · ${esc(s.group)}</p>
      <h2 class="display">${esc(d.headline)}</h2>
      <ul class="chips"><li class="chip">${esc(s.date_label)}</li><li class="chip">${esc(WINDOW[s.time_window] || s.time_window)}</li>
        <li class="chip">${s.players} player${s.players === 1 ? "" : "s"}</li>${s.max_price_per_player ? `<li class="chip">Up to ${money(s.max_price_per_player)} each</li>` : ""}${needChips(s.needs)}</ul>
    </div></div>
    ${s.note ? `<p class="note-small">${esc(s.note)}</p>` : ""}
    <p class="note">${esc(d.note)}</p>
    ${safety}
    <div class="rec-list">${d.recommendations.map(recCard).join("")}</div>
    ${waitlist}${others}
    <details class="panel"><summary>How CaddieSync decided · ${d.trace.length} steps</summary><div class="panel-body"><ol class="trace">${trace}</ol></div></details>
    <details class="panel"><summary>Cost of this search · $${u.cost_usd.toFixed(4)}</summary><div class="panel-body">
      <div class="stats">
        <div class="stat"><div class="v">${tok(u.input_tokens)}</div><div class="k">input tokens</div></div>
        <div class="stat"><div class="v">${tok(u.cached_input_tokens)}</div><div class="k">of those, cached</div></div>
        <div class="stat"><div class="v">${tok(u.output_tokens)}</div><div class="k">output tokens</div></div>
        <div class="stat stat-hi"><div class="v">$${u.cost_usd.toFixed(4)}</div><div class="k">cost of this search</div></div>
        <div class="stat"><div class="v">${u.analyst_cache_hits}/${u.analyst_cache_hits + u.analyst_fresh}</div><div class="k">course scores from cache</div></div>
        <div class="stat stat-hi"><div class="v">${tok(u.saved_by_cache.input_tokens + u.saved_by_cache.output_tokens)}</div><div class="k">tokens saved by cache</div></div>
      </div>
      <div class="table-scroll"><table class="calls"><thead><tr><th>Model call</th><th>Input</th><th>Cached</th><th>Output</th><th>Cost</th></tr></thead><tbody>${calls}</tbody></table></div>
      <p class="note-small" style="margin-top:12px">Real token counts reported by the API for this run · ${u.elapsed_ms} ms · tee times simulated.</p>
    </div></details>
  </div>`;
  results.hidden = false;
  $$("[data-slot]", results).forEach((b) => b.addEventListener("click", () => joinWaitlist(b)));
  results.scrollIntoView({ behavior: "smooth", block: "start" });
  results.focus({ preventScroll: true });
}

async function joinWaitlist(btn) {
  btn.disabled = true;
  try {
    const r = await fetch("/api/waitlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ slot_id: btn.dataset.slot, players: lastPlayers }) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error);
    btn.textContent = `On waitlist · #${d.position}`;
  } catch { btn.disabled = false; btn.textContent = "Try again"; }
}

/* ---------- Courses ---------- */
fetch("/api/courses").then((r) => r.json()).then((list) => {
  $("#course-grid").innerHTML = list.map((c) => {
    const pct = Math.round((c.known_access_facts / c.total_access_facts) * 100);
    return `<article class="course">
      <h3>${esc(c.name)}</h3>
      <p class="meta">${esc(c.town)} · ${c.holes} holes · par ${c.par ?? "?"} · ${esc(c.access.replace("_", "-"))}</p>
      <span class="terrain">${c.terrain ? TERRAIN[c.terrain] : "Terrain unknown"}</span>
      <div class="known"><div class="known-bar" role="img" aria-label="${c.known_access_facts} of ${c.total_access_facts} accessibility facts known"><i style="width:${pct}%"></i></div>
        <small>${c.known_access_facts} of ${c.total_access_facts} accessibility facts known · ${c.sources} source${c.sources === 1 ? "" : "s"}${c.verified_by_phone ? " · verified by phone" : ""}</small></div>
    </article>`;
  }).join("");
}).catch(() => { $("#course-grid").innerHTML = '<p class="muted">Couldn\'t load the course list.</p>'; });
