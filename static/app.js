const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => (n == null ? "" : `$${Number(n).toFixed(0)}`);
const tok = (n) => Number(n || 0).toLocaleString();

const form = $("#search-form"), msg = $("#message"), go = $("#go"), statusEl = $("#status"), results = $("#results");
const STEPS = ["Checking your message", "Profiler is reading your request", "Tee-Time Sniper is scanning tee sheets",
  "Course Analyst is scoring courses", "Concierge is picking the best options"];
const NEED_LABELS = {
  walking_ability: { limited: "Limited walking", minimal: "Minimal walking" },
  terrain: { avoid_steep: "No steep hills", flat_only: "Flat only" },
  forced_carries: { avoid_long: "No long carries", avoid_all: "No forced carries" },
  bunkers: { avoid_deep: "No deep bunkers" },
  cart_near_greens: { true: "Cart near greens" }, adaptive_cart: { true: "Adaptive cart" },
  shorter_course: { true: "Shorter course" },
};
const VERDICT = { great_fit: "Great fit", workable: "Workable", poor_fit: "Poor fit" };
let lastPlayers = 1;

document.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => { msg.value = b.dataset.example; msg.focus(); }));
fetch("/api/health").then((r) => r.json()).then((h) => { $("#model-line").textContent = `Model: ${h.model} · ${h.courses} courses`; }).catch(() => {});

let timer;
function showProgress() {
  statusEl.hidden = false; statusEl.className = "status";
  statusEl.innerHTML = `<ul class="steps">${STEPS.map((s) => `<li><span class="dot"></span>${s}</li>`).join("")}</ul>`;
  const items = [...statusEl.querySelectorAll("li")];
  let i = 0;
  const tick = () => { items.forEach((li, j) => li.className = j < i ? "done" : j === i ? "on" : ""); if (i < items.length - 1) i++; };
  tick(); timer = setInterval(tick, 1800);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = msg.value.trim();
  if (!message) return;
  go.disabled = true; go.textContent = "Searching…"; results.hidden = true; showProgress();
  try {
    const r = await fetch("/api/search", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "Search failed.");
    statusEl.hidden = true; render(data);
  } catch (err) {
    statusEl.className = "status error"; statusEl.innerHTML = `<strong>Couldn't finish that search.</strong> ${esc(err.message)}`;
  } finally {
    clearInterval(timer); go.disabled = false; go.textContent = "Find my tee time";
  }
});

function needPills(needs) {
  return Object.entries(needs).map(([k, v]) => NEED_LABELS[k]?.[String(v)]).filter(Boolean)
    .map((t) => `<li class="pill need">${esc(t)}</li>`).join("");
}

function card(r, i) {
  const findings = r.findings.map((f) => `<li><span class="fx ${f.effect}">${f.effect === "helps" ? "✓" : f.effect === "hurts" ? "!" : "·"}</span><span>${esc(f.finding)}</span></li>`).join("");
  const verify = r.verify_with_course.length ? `<div class="verify">Call to confirm: ${r.verify_with_course.map(esc).join(", ")}</div>` : "";
  return `<article class="card ${i === 0 ? "best" : ""}">
    <div>
      <h3>${esc(r.course_name)}</h3><div class="town">${esc(r.town || "")}</div>
      <div class="facts"><span><b>${esc(r.tee_time)}</b></span><span><b>${money(r.price_per_player)}</b> per player</span>
        <span>${r.open_spots} open spot${r.open_spots === 1 ? "" : "s"}</span>${r.deal ? '<span class="deal">Deal</span>' : ""}</div>
      <p class="why">${esc(r.why_it_fits)}</p>
      ${r.heads_up ? `<p class="heads">Heads up: ${esc(r.heads_up)}</p>` : ""}
      <ul class="findings">${findings}</ul>${verify}
      <div class="card-actions">
        ${r.website ? `<a class="btn-ghost" href="${esc(r.website)}" target="_blank" rel="noopener">Course website ↗</a>` : ""}
        ${r.phone ? `<a class="btn-ghost" href="tel:${esc(r.phone)}">Call ${esc(r.phone)}</a>` : ""}
      </div>
    </div>
    <div class="score" aria-label="Accessibility match ${r.accessibility_match_score} out of 100">
      <div class="ring" style="--p:${r.accessibility_match_score}"><span>${r.accessibility_match_score}</span></div>
      <small>accessibility match</small>
    </div>
  </article>`;
}

function render(d) {
  lastPlayers = d.searched.players;
  const s = d.searched, u = d.usage;
  const facts = d.safety.filter((x) => x.layer === "code: fact check");
  const main = d.safety.filter((x) => x.layer !== "code: fact check");
  const item = (x) => `<li><span class="layer">${esc(x.layer)}</span>: ${esc(x.event)}</li>`;
  const safety = d.safety.length ? `<div class="safety" ${main.length ? 'role="alert"' : ""}><h3>🛡️ Safety nets caught ${d.safety.length} thing${d.safety.length === 1 ? "" : "s"}</h3>
      ${main.length ? `<ul>${main.map(item).join("")}</ul>` : ""}
      ${facts.length ? `<details><summary style="cursor:pointer;color:var(--muted);font-size:.9rem;margin-top:6px">Fact check: ${facts.length} AI claim${facts.length === 1 ? "" : "s"} about unknown course facts removed or score capped</summary><ul>${facts.map(item).join("")}</ul></details>` : ""}</div>` : "";
  const waitlist = d.waitlist_options.length ? `<div class="sub"><h3>Prime times that are full, with an open waitlist</h3><div class="mini">
      ${d.waitlist_options.map((w) => `<div class="mini-row"><span><b>${esc(w.course_name)}</b> · ${esc(w.tee_time)} · ${money(w.price_per_player)} <span class="muted">· match ${w.score}</span></span>
        <button class="btn-ghost" data-slot="${esc(w.slot_id)}">Join waitlist</button></div>`).join("")}</div></div>` : "";
  const others = d.other_courses.length ? `<div class="sub"><h3>Other courses CaddieSync checked</h3><div class="mini">
      ${d.other_courses.map((o) => `<div class="mini-row"><span><b>${esc(o.course_name)}</b> <span class="muted">· ${esc(o.one_line)}</span></span>
        <span class="badge ${o.verdict}">${VERDICT[o.verdict]} · ${o.score}</span></div>`).join("")}</div></div>` : "";
  const trace = d.trace.map((t) => `<li><span class="n">${t.step}</span><div>
      <div><span class="tag ${t.kind === "ai" ? "tag-ai" : "tag-code"}">${t.kind === "ai" ? "AI" : "code"}</span><span class="who">${esc(t.who)}</span>
      <span class="meta"> · ${t.ms} ms${t.usage && !Array.isArray(t.usage) ? ` · ${tok(t.usage.input_tokens)} in (${tok(t.usage.cached_input_tokens)} cached) / ${tok(t.usage.output_tokens)} out` : ""}</span></div>
      <div>${esc(t.summary)}</div>
      ${t.output || t.scores ? `<pre>${esc(JSON.stringify(t.output || { scores: t.scores, from_cache: t.cache_hits, fresh: t.fresh }, null, 2))}</pre>` : ""}
    </div></li>`).join("");
  const calls = u.calls.map((c) => `<tr><td>${esc(c.agent)}${c.label ? ` · ${esc(c.label)}` : ""}</td><td>${tok(c.input_tokens)}</td><td>${tok(c.cached_input_tokens)}</td><td>${tok(c.output_tokens)}</td><td>$${c.cost_usd.toFixed(5)}</td></tr>`).join("");

  results.innerHTML = `
    <div class="result-head"><div><h2>${esc(d.headline)}</h2>
      <ul class="searched"><li class="pill">${esc(s.date_label)}</li><li class="pill">${esc(s.time_window)}</li>
        <li class="pill">${s.players} player${s.players === 1 ? "" : "s"}</li>${s.max_price_per_player ? `<li class="pill">≤ ${money(s.max_price_per_player)} each</li>` : ""}${needPills(s.needs)}</ul></div></div>
    ${s.note ? `<p class="note">${esc(s.note)}</p>` : ""}
    <p class="note">${esc(d.note)}</p>
    ${safety}
    <div class="cards">${d.recommendations.map(card).join("")}</div>
    ${waitlist}${others}
    <details class="panel"><summary>How CaddieSync decided (${d.trace.length} steps)</summary><div class="body"><ol class="trace">${trace}</ol></div></details>
    <details class="panel"><summary>Cost of this search: $${u.cost_usd.toFixed(4)} · ${tok(u.input_tokens + u.output_tokens)} tokens</summary><div class="body">
      <div class="usage-grid">
        <div class="stat"><div class="v">${tok(u.input_tokens)}</div><div class="k">input tokens</div></div>
        <div class="stat"><div class="v">${tok(u.cached_input_tokens)}</div><div class="k">of those, cached input</div></div>
        <div class="stat"><div class="v">${tok(u.output_tokens)}</div><div class="k">output tokens</div></div>
        <div class="stat hi"><div class="v">$${u.cost_usd.toFixed(4)}</div><div class="k">cost of this search</div></div>
        <div class="stat"><div class="v">${u.analyst_cache_hits}/${u.analyst_cache_hits + u.analyst_fresh}</div><div class="k">course scores from cache</div></div>
        <div class="stat hi"><div class="v">${tok(u.saved_by_cache.input_tokens + u.saved_by_cache.output_tokens)}</div><div class="k">tokens saved by cache</div></div>
      </div>
      <div class="table-scroll"><table class="calls"><thead><tr><th>Model call</th><th>Input</th><th>Cached</th><th>Output</th><th>Cost</th></tr></thead><tbody>${calls}</tbody></table></div>
      <p class="meta" style="color:var(--faint);font-size:.82rem;margin:10px 0 0">Real token counts reported by the API for this run · ${u.elapsed_ms} ms · tee times simulated.</p>
    </div></details>`;
  results.hidden = false;
  results.querySelectorAll("[data-slot]").forEach((b) => b.addEventListener("click", () => joinWaitlist(b)));
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function joinWaitlist(btn) {
  btn.disabled = true;
  try {
    const r = await fetch("/api/waitlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ slot_id: btn.dataset.slot, players: lastPlayers }) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error);
    btn.textContent = `On waitlist · #${d.position}`;
  } catch (e) { btn.disabled = false; btn.textContent = "Try again"; }
}
