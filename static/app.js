const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const tok = (n) => Number(n || 0).toLocaleString();
const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
                set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);

const form = $("#find"), msg = $("#message"), go = $("#go"), statusEl = $("#status"), results = $("#results");
const STEPS = [["Guard", "Checking your message"], ["Profiler", "Reading who's playing"], ["Locator", "Finding courses nearby"],
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
const TERRAIN_COLOR = { flat: "#2e9d5a", gently_rolling: "#9ccc3d", hilly: "#f0a020", severe: "#d6453d" };
const AMEN = { driving_range: "Driving range", practice_green: "Practice green", simulator: "Simulator", pro_shop: "Pro shop", food_drink: "Food & drink", clubhouse: "Clubhouse" };
const ACCESS = { public: "Public", municipal: "Public (town)", semi_private: "Semi-private", private: "Private", unknown: "Access: check" };
const TILES = { url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' };
let lastResult = null;

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

fetch("/api/health").then((r) => r.json()).then((h) => {
  $("#model-line").textContent = `Model: ${h.model}`;
  if (h.courses) $("#stat-courses").textContent = tok(h.courses);
  if (h.holes_measured) $("#stat-holes").textContent = tok(h.holes_measured);
}).catch(() => {});

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
    const useProfile = !!(acct.profile && acct.useProfile);
    const body = { message, use_profile: useProfile, profile: useProfile && !acct.user ? acct.profile : null };
    const r = await fetch("/api/search", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "Search failed.");
    statusEl.hidden = true; render(data);
  } catch (err) {
    statusEl.className = "status-band status-error";
    statusEl.innerHTML = `<div class="wrap" role="alert"><p class="kicker">Couldn't finish that search</p><p>${esc(err.message)}</p></div>`;
  } finally {
    clearInterval(timer); go.disabled = false; go.firstChild.textContent = "Find my courses ";
  }
});

function needChips(needs) {
  return Object.entries(needs).map(([k, v]) => NEED_LABELS[k]?.[String(v)]).filter(Boolean).map((t) => `<li class="chip chip-need">${esc(t)}</li>`).join("");
}

function recCard(r, i) {
  const fx = { helps: "✓", hurts: "!", neutral: "•" };
  const findings = r.findings.map((f) => `<li><span class="fx ${f.effect}" aria-label="${f.effect}">${fx[f.effect]}</span><span>${esc(f.finding)}</span></li>`).join("");
  const amenEntries = Object.entries(r.amenities || {});
  const missingWants = (r.wants || []).filter((w) => !(r.amenities || {})[w]);
  const amenLabel = (k, v) => k === "driving_range" && typeof v === "string" ? `Driving range (${v})` : k === "food_drink" && typeof v === "string" ? v : AMEN[k];
  const extras = amenEntries.length || missingWants.length ? `<div class="amenities" aria-label="On-site extras">
      ${amenEntries.map(([k, v]) => `<span class="amen ${(r.wants || []).includes(k) ? "amen-want" : ""}">✓ ${esc(amenLabel(k, v))}</span>`).join("")}
      ${missingWants.map((w) => `<span class="amen amen-ask">${esc(AMEN[w])}: not on the map, ask</span>`).join("")}</div>` : "";
  const measured = r.highlights.length ? `<div class="measured"><p class="measured-title">Measured from the map</p><ul>${r.highlights.map((h) => `<li>${esc(h)}</li>`).join("")}</ul></div>` : "";
  const script = r.call_script.length ? `<details class="call-script"><summary>What to ask when you call</summary><ol>${r.call_script.map((q) => `<li>${esc(q)}</li>`).join("")}</ol></details>` : "";
  const book = safeUrl(r.booking_url), site = safeUrl(r.website);
  const facts = [
    ["Distance", `${r.distance_mi} mi`], ["Holes", r.holes ? `${r.holes}${r.par ? ` · par ${r.par}` : ""}` : "—"],
    ["Price", r.price_tier ? r.price_tier : "Not listed"], ["Access", ACCESS[r.access] || "Check"],
  ];
  return `<article class="rec ${i === 0 ? "rec-best" : ""}" aria-label="${esc(r.course_name)}">
    <div class="rec-main">
      <span class="rec-flag ${r.verdict === "poor_fit" ? "rec-flag-poor" : ""}">${r.verdict === "poor_fit" ? "Not a good fit" : i === 0 ? "Best match" : `Pick ${i + 1}`}</span>
      <h3>${esc(r.course_name)}</h3>
      <p class="rec-town">${esc(r.town || "")}, ${esc(r.state)}${r.verified_by_phone ? ' · <span class="verified">Verified by phone</span>' : ""}</p>
      <dl class="rec-facts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
      ${r.price_note ? `<p class="price-note">${esc(r.price_note)}</p>` : ""}
      <p class="why">${esc(r.why_it_fits)}</p>
      ${r.heads_up ? `<p class="heads"><b>Heads up:</b> ${esc(r.heads_up)}</p>` : ""}
      ${extras}
      ${measured}
      <ul class="findings">${findings}</ul>
      ${r.verify_with_course.length ? `<p class="verify"><b>Call to confirm:</b> ${r.verify_with_course.map(esc).join(" · ")}</p>` : ""}
      ${script}
      <div class="rec-actions">
        ${book ? `<a class="btn btn-green" href="${esc(book)}" target="_blank" rel="noopener">Book a tee time <span aria-hidden="true">↗</span></a>`
               : `<a class="btn btn-green" href="${esc(r.tee_time_search_url)}" target="_blank" rel="noopener">Find tee times <span aria-hidden="true">↗</span></a>`}
        ${site ? `<a class="btn btn-outline" href="${esc(site)}" target="_blank" rel="noopener">Website <span aria-hidden="true">↗</span></a>` : ""}
        ${r.phone ? `<a class="btn btn-outline" href="tel:${esc(r.phone)}">Call ${esc(r.phone)}</a>` : ""}
        <a class="btn btn-outline" href="${esc(r.directions_url)}" target="_blank" rel="noopener">Directions <span aria-hidden="true">↗</span></a>
      </div>
      ${r.ferry ? '<p class="heads"><b>Ferry needed:</b> this course is on an island. Check ferry times before you plan the day.</p>' : ""}
      ${r.map_detail !== "full" ? '<p class="note-small">This course is only partly mapped, so some facts are unknown.</p>' : ""}
    </div>
    <div class="rec-side">
      <div class="ring" style="--p:${r.accessibility_match_score}" role="img" aria-label="Accessibility match ${r.accessibility_match_score} out of 100"><span>${r.accessibility_match_score}</span></div>
      <div><small>Accessibility match</small><br><span class="verdict">${VERDICT[r.verdict]}</span></div>
    </div>
  </article>`;
}

function render(d) {
  lastResult = d;
  const s = d.searched, u = d.usage;
  const facts = d.safety.filter((x) => x.layer === "code: fact check");
  const main = d.safety.filter((x) => x.layer !== "code: fact check");
  const item = (x) => `<li><span class="layer">${esc(x.layer)}</span>: ${esc(x.event)}</li>`;
  const shield = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 4 5v6c0 5 3.4 9.3 8 11 4.6-1.7 8-6 8-11V5z" fill="currentColor"/></svg>';
  const safety = d.safety.length ? `<div class="safety" ${main.length ? 'role="alert"' : ""}>
      <h3>${shield} ${main.length ? `Safety nets caught ${main.length} thing${main.length === 1 ? "" : "s"}` : "Fact-checked"}</h3>
      ${main.length ? `<ul>${main.map(item).join("")}</ul>` : ""}
      ${facts.length ? `<details><summary>${facts.length} AI claim${facts.length === 1 ? "" : "s"} about facts the course data doesn't have were removed, or the score was capped</summary><ul>${facts.map(item).join("")}</ul></details>` : ""}</div>` : "";
  const others = d.other_courses.length ? `<div class="sub"><h3>Other courses CaddieSync checked</h3><div class="rows">
      ${d.other_courses.map((o) => `<div class="row"><span><b>${esc(o.course_name)}</b> <span class="muted">· ${esc(o.town || "")}, ${esc(o.state)} · ${o.distance_mi} mi · ${esc(o.one_line)}</span></span>
        <span class="badge ${o.verdict}">${VERDICT[o.verdict]} · ${o.score}</span></div>`).join("")}</div></div>` : "";
  const trace = d.trace.map((t) => `<li><span class="n">${t.step}</span><div>
      <div><span class="tag ${t.kind === "ai" ? "tag-ai" : "tag-code"}">${t.kind === "ai" ? "AI" : "Code"}</span><span class="who">${esc(t.who)}</span>
      <span class="meta"> · ${t.ms} ms${t.usage && !Array.isArray(t.usage) ? ` · ${tok(t.usage.input_tokens)} in (${tok(t.usage.cached_input_tokens)} cached) / ${tok(t.usage.output_tokens)} out` : ""}</span></div>
      <div>${esc(t.summary)}</div>
      ${t.output || t.scores || t.shortlist ? `<pre>${esc(JSON.stringify(t.output || (t.shortlist ? { shortlist_pre_scores: t.shortlist } : { scores: t.scores, from_cache: t.cache_hits, fresh: t.fresh }), null, 2))}</pre>` : ""}
    </div></li>`).join("");
  const calls = u.calls.map((c) => `<tr><td>${esc(c.agent)}${c.label ? ` · ${esc(c.label)}` : ""}</td><td>${tok(c.input_tokens)}</td><td>${tok(c.cached_input_tokens)}</td><td>${tok(c.output_tokens)}</td><td>$${c.cost_usd.toFixed(5)}</td></tr>`).join("");

  results.innerHTML = `<div class="wrap">
    <div class="result-head"><div>
      <p class="kicker kicker-dark">${s.trip ? "Your golf trip" : "Your round"} · ${esc(s.group)}</p>
      <h2 class="display">${esc(d.headline)}</h2>
      <ul class="chips"><li class="chip">${esc(s.place)} · ${s.radius_mi} mi</li><li class="chip">${s.courses_considered} courses considered</li>
        <li class="chip">${s.players} player${s.players === 1 ? "" : "s"}</li>${s.price_tier_max ? `<li class="chip">Up to ${esc(s.price_tier_max)}</li>` : ""}${s.holes !== "either" ? `<li class="chip">${s.holes} holes</li>` : ""}${needChips(s.needs)}</ul>
    </div>
    ${d.recommendations.length ? `<div class="result-tools"><button type="button" class="btn btn-outline" id="copy-plan">Copy ${s.trip ? "trip plan" : "list"}</button><button type="button" class="btn btn-outline" id="print-plan">Print</button></div>` : ""}
    </div>
    ${s.place_note ? `<p class="note-small">${esc(s.place_note)}</p>` : ""}
    ${s.used_profile ? `<p class="note-small">Used your saved profile${s.from_profile.length ? ` (kept: ${esc(s.from_profile.join("; "))})` : ""}.</p>` : ""}
    <p class="note">${esc(d.note)}</p>
    ${safety}
    ${d.recommendations.length ? '<div id="results-map" class="results-map" role="region" aria-label="Map of your recommended courses"></div>' : ""}
    <div class="rec-list">${d.recommendations.map(recCard).join("")}</div>
    ${others}
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
      <p class="note-small" style="margin-top:12px">Real token counts reported by the API for this run · ${u.elapsed_ms} ms.</p>
    </div></details>
  </div>`;
  results.hidden = false;
  if (d.recommendations.length) drawResultsMap(d);
  $("#copy-plan")?.addEventListener("click", copyPlan);
  $("#print-plan")?.addEventListener("click", () => window.print());
  results.scrollIntoView({ behavior: "smooth", block: "start" });
  results.focus({ preventScroll: true });
}

function drawResultsMap(d) {
  if (!window.L) return;
  const el = $("#results-map");
  const m = L.map(el, { scrollWheelZoom: false }).setView([d.searched.lat, d.searched.lon], 9);
  L.tileLayer(TILES.url, { attribution: TILES.attribution, maxZoom: 18 }).addTo(m);
  const pts = [];
  L.circle([d.searched.lat, d.searched.lon], { radius: d.searched.radius_mi * 1609, color: "#1f3e2d", weight: 1, fillOpacity: 0.04 }).addTo(m);
  d.other_courses.forEach((o) => {
    L.circleMarker([o.lat, o.lon], { radius: 6, color: "#55625a", weight: 1, fillColor: "#bdb6a3", fillOpacity: 0.9 })
      .bindTooltip(`${esc(o.course_name)} · ${o.score}`).addTo(m);
  });
  d.recommendations.forEach((r, i) => {
    const icon = L.divIcon({ className: "pin-num", html: `<span>${i + 1}</span>`, iconSize: [30, 30], iconAnchor: [15, 15] });
    L.marker([r.lat, r.lon], { icon, title: r.course_name, alt: `${i + 1}. ${r.course_name}` })
      .bindPopup(`<b>${i + 1}. ${esc(r.course_name)}</b><br>${esc(r.town || "")}, ${esc(r.state)} · match ${r.accessibility_match_score}`).addTo(m);
    pts.push([r.lat, r.lon]);
  });
  if (pts.length > 1) m.fitBounds(pts, { padding: [40, 40], maxZoom: 12 }); else m.setView(pts[0], 11);
}

async function copyPlan() {
  const d = lastResult; if (!d) return;
  const lines = [`CaddieSync: ${d.headline}`, `${d.searched.group}`, ""];
  d.recommendations.forEach((r, i) => {
    lines.push(`${i + 1}. ${r.course_name} (${r.town || ""}, ${r.state}) · ${r.distance_mi} mi · match ${r.accessibility_match_score}/100${r.price_tier ? ` · ${r.price_tier}` : ""}`);
    lines.push(`   ${r.why_it_fits}`);
    if (r.heads_up) lines.push(`   Heads up: ${r.heads_up}`);
    if (r.phone) lines.push(`   Phone: ${r.phone}`);
    const link = safeUrl(r.booking_url) || safeUrl(r.website); if (link) lines.push(`   ${link}`);
    lines.push(`   Directions: ${r.directions_url}`);
    if (r.call_script.length) lines.push(`   Ask: ${r.call_script.join(" / ")}`);
    lines.push("");
  });
  try { await navigator.clipboard.writeText(lines.join("\n")); toast("Copied. Paste it into a text or email."); }
  catch { toast("Couldn't copy. Try the Print button instead."); }
}

/* ---------- Course map explorer ---------- */
let allCourses = [], stateFilter = "", exploreMap = null, exploreLayer = null;
function courseCard(c) {
  return `<article class="course">
    <h3>${esc(c.name)}</h3>
    <p class="meta">${esc(c.town || "")}, ${c.state}${c.holes ? ` · ${c.holes} holes` : ""}${c.price_tier ? ` · ${c.price_tier}` : ""}</p>
    <span class="terrain">${c.terrain ? TERRAIN[c.terrain] : c.map_detail === "outline" ? "Not measured" : "Terrain unknown"}</span>
    <div class="known"><div class="known-bar" role="img" aria-label="${c.known} of ${c.of} accessibility facts known"><i style="width:${Math.round((c.known / c.of) * 100)}%"></i></div>
      ${(c.amen || []).length ? `<p class="meta course-amen">${c.amen.map((a) => esc(AMEN[a] || a)).join(" · ")}</p>` : ""}
      <small>${c.known} of ${c.of} accessibility facts known${c.total_climb_ft != null ? ` · ${tok(c.total_climb_ft)} ft of climbing` : ""}${c.verified_by_phone ? " · verified by phone" : ""}</small></div>
  </article>`;
}
let amenFilter = "";
function filteredCourses() {
  const q = ($("#course-search").value || "").trim().toLowerCase();
  return allCourses.filter((c) => (!stateFilter || c.state === stateFilter) && (!amenFilter || (c.amen || []).includes(amenFilter)) && (!q || c.name.toLowerCase().includes(q) || (c.town || "").toLowerCase().includes(q)));
}
function drawExplorer() {
  const list = filteredCourses();
  const shown = list.slice(0, 24);
  $("#course-list").innerHTML = shown.map(courseCard).join("")
    + (list.length > shown.length ? `<p class="muted list-more">Showing ${shown.length} of ${tok(list.length)}. Search or zoom the map to narrow it down.</p>` : "")
    + (list.length ? "" : '<p class="muted">No courses match.</p>');
  if (!exploreMap) return;
  exploreLayer.clearLayers();
  list.forEach((c) => {
    L.circleMarker([c.lat, c.lon], { radius: 5, weight: 1, color: "#1f3e2d", fillColor: TERRAIN_COLOR[c.terrain] || "#9a9a9a", fillOpacity: 0.9 })
      .bindPopup(`<b>${esc(c.name)}</b><br>${esc(c.town || "")}, ${c.state}<br>${c.terrain ? TERRAIN[c.terrain] : "Not measured"}${c.total_climb_ft != null ? ` · ${tok(c.total_climb_ft)} ft climb` : ""}${c.holes ? ` · ${c.holes} holes` : ""}${(c.amen || []).length ? `<br>${c.amen.map((a) => esc(AMEN[a] || a)).join(" · ")}` : ""}`)
      .addTo(exploreLayer);
  });
  if (stateFilter && list.length) exploreMap.fitBounds(list.map((c) => [c.lat, c.lon]), { padding: [20, 20] });
}
$$(".state-chip:not(.amen-chip)").forEach((b) => b.addEventListener("click", () => {
  stateFilter = b.dataset.state; $$(".state-chip:not(.amen-chip)").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  if (!stateFilter && exploreMap) exploreMap.fitBounds([[41.0, -73.8], [47.4, -66.9]]);
  drawExplorer();
}));
let searchT; $("#course-search").addEventListener("input", () => { clearTimeout(searchT); searchT = setTimeout(drawExplorer, 150); });

const mapObserver = new IntersectionObserver((entries) => {   // build the big map only when it scrolls into view
  if (!entries.some((e) => e.isIntersecting) || exploreMap || !window.L) return;
  exploreMap = L.map("course-map", { scrollWheelZoom: false }).fitBounds([[41.0, -73.8], [47.4, -66.9]]);
  L.tileLayer(TILES.url, { attribution: TILES.attribution, maxZoom: 18 }).addTo(exploreMap);
  exploreLayer = L.layerGroup().addTo(exploreMap);
  drawExplorer();
  mapObserver.disconnect();
}, { rootMargin: "200px" });
mapObserver.observe($("#course-map"));

fetch("/api/courses").then((r) => r.json()).then((list) => {
  allCourses = list.sort((a, b) => (b.known - a.known) || a.name.localeCompare(b.name));
  drawExplorer();
}).catch(() => { $("#course-list").innerHTML = '<p class="muted">Couldn\'t load the course list.</p>'; });

/* ---------- Free accounts + saved profile ---------- */
const acct = { user: null, profile: null, useProfile: store.get("cs-use-profile") !== "0", staleOwner: "" };
const dlg = $("#account-dialog"), authView = $("#auth-view"), authForm = $("#auth-form"), pform = $("#profile-form");
let authMode = "signup";

function loadLocal() {
  try { return { profile: JSON.parse(store.get("cs-profile") || "null"), owner: store.get("cs-profile-owner") || "" }; }
  catch { return { profile: null, owner: "" }; }
}
function saveLocal(profile, owner) { store.set("cs-profile", JSON.stringify(profile)); store.set("cs-profile-owner", owner || ""); }
function clearLocal() { try { localStorage.removeItem("cs-profile"); localStorage.removeItem("cs-profile-owner"); } catch {} }

function toast(text) {
  const t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); t.textContent = text;
  document.body.append(t); setTimeout(() => t.remove(), 3200);
}

async function api(path, method = "GET", data) {
  const r = await fetch(path, { method, headers: data ? { "Content-Type": "application/json" } : {}, body: data ? JSON.stringify(data) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    const m = typeof d.detail === "string" ? d.detail : Array.isArray(d.detail) ? "Please check the highlighted fields." : (d.error || "Something went wrong.");
    throw new Error(m);
  }
  return d;
}

function needSummary(p) {
  const out = [];
  if (p.walking_ability !== "full") out.push(NEED_LABELS.walking_ability[p.walking_ability]);
  if (p.terrain !== "any") out.push(NEED_LABELS.terrain[p.terrain]);
  if (p.forced_carries !== "any") out.push(NEED_LABELS.forced_carries[p.forced_carries]);
  if (p.bunkers === "avoid_deep") out.push("No deep bunkers");
  if (p.cart_near_greens) out.push("Cart near greens");
  if (p.adaptive_cart) out.push("Adaptive cart");
  if (p.shorter_course) out.push("Shorter course");
  return out;
}

function renderAccount() {
  $("#account-label").textContent = acct.user ? acct.user : acct.profile ? "My profile" : "Sign in";
  $("#account-btn").setAttribute("aria-label", acct.user ? `Your profile, signed in as ${acct.user}` : acct.profile ? "Your profile" : "Sign in or create a free profile");
  const bar = $("#profile-bar");
  if (!acct.profile) {
    bar.hidden = false; bar.className = "profile-cta";
    bar.innerHTML = `<span>Save your group, home town and needs once and skip retyping them. Free, with no email needed.</span>
      <button type="button" class="link-btn" data-open-profile>Create a free profile</button>`;
  } else {
    const p = acct.profile, chips = needSummary(p);
    const bits = [`${p.usual_players} player${p.usual_players === 1 ? "" : "s"}`, p.home_town ? `near ${p.home_town}` : null,
      p.travel_miles ? `${p.travel_miles} mi` : null, p.price_tier_max ? `up to ${p.price_tier_max}` : null].filter(Boolean).join(" · ");
    bar.hidden = false; bar.className = "profile-bar";
    bar.innerHTML = `<div class="pb-top"><div class="pb-who">Using ${esc(p.display_name || "your profile")}<small>${esc(bits)}${acct.user ? "" : " · saved on this device"}</small></div>
        <div class="pb-actions"><label class="switch"><input type="checkbox" id="use-profile" ${acct.useProfile ? "checked" : ""}> Use it</label>
        <button type="button" class="link-btn" data-open-profile>Edit</button></div></div>
      ${chips.length ? `<div class="pb-chips">${chips.map((c) => `<span>${esc(c)}</span>`).join("")}</div>` : ""}
      ${acct.staleOwner ? `<p class="pb-warn">You're signed out of "${esc(acct.staleOwner)}". Sign in again, or if the account was reset, create it again with the same name and this profile comes back.</p>` : ""}`;
    $("#use-profile").addEventListener("change", (e) => { acct.useProfile = e.target.checked; store.set("cs-use-profile", e.target.checked ? "1" : "0"); });
  }
  $$("[data-open-profile]", bar).forEach((b) => b.addEventListener("click", openDialog));
}

const BLANK = { display_name: "", home_town: "", usual_players: 1, travel_miles: 25, price_tier_max: null, holes: "either",
  walking_ability: "full", terrain: "any", forced_carries: "any", bunkers: "any", cart_near_greens: false, adaptive_cart: false,
  shorter_course: false, skill: "unknown", notes: "" };

function fillForm(p) {
  const f = pform.elements, d = { ...BLANK, ...(p || {}) };
  f.display_name.value = d.display_name; f.home_town.value = d.home_town;
  f.travel_miles.value = String([10, 25, 40, 60, 100].reduce((a, b) => (Math.abs(b - d.travel_miles) < Math.abs(a - d.travel_miles) ? b : a)));
  f.price_tier_max.value = d.price_tier_max || ""; f.skill.value = d.skill; f.notes.value = d.notes;
  for (const name of ["usual_players", "holes", "walking_ability", "terrain", "forced_carries"]) {
    $$(`input[name="${name}"]`, pform).forEach((r) => { r.checked = r.value === String(d[name]); });
  }
  f.bunkers.checked = d.bunkers === "avoid_deep";
  for (const name of ["cart_near_greens", "adaptive_cart", "shorter_course"]) f[name].checked = !!d[name];
}

function readForm() {
  const f = pform.elements, radio = (n) => $(`input[name="${n}"]:checked`, pform)?.value;
  return {
    display_name: f.display_name.value.trim(), home_town: f.home_town.value.trim(),
    usual_players: Number(radio("usual_players") || 1), travel_miles: Number(f.travel_miles.value || 25),
    price_tier_max: f.price_tier_max.value || null, holes: radio("holes") || "either",
    walking_ability: radio("walking_ability") || "full", terrain: radio("terrain") || "any", forced_carries: radio("forced_carries") || "any",
    bunkers: f.bunkers.checked ? "avoid_deep" : "any", cart_near_greens: f.cart_near_greens.checked, adaptive_cart: f.adaptive_cart.checked,
    shorter_course: f.shorter_course.checked, skill: f.skill.value, notes: f.notes.value.trim(),
  };
}

function showView(view) {
  authView.hidden = view !== "auth";
  pform.hidden = view !== "profile";
  if (view === "profile") {
    fillForm(acct.profile);
    $("#profile-status").innerHTML = acct.user
      ? `Signed in as <strong>${esc(acct.user)}</strong>. Your profile is saved to your free account and on this device.`
      : `Saved on <strong>this device only</strong>. <button type="button" class="link-btn" id="make-account">Create a free account</button> to keep it safe.`;
    $("#signout-btn").hidden = !acct.user; $("#delete-btn").hidden = !acct.user; $("#clear-guest-btn").hidden = !!acct.user || !acct.profile;
    $("#make-account")?.addEventListener("click", () => { setAuthMode("signup"); showView("auth"); });
    $("#profile-error").hidden = true;
  }
}

function openDialog() { showView(acct.user || acct.profile ? "profile" : "auth"); dlg.showModal(); }
$("#account-btn").addEventListener("click", openDialog);
$$("[data-close]", dlg).forEach((b) => b.addEventListener("click", () => dlg.close()));
dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });

function setAuthMode(mode) {
  authMode = mode;
  $("#tab-signup").setAttribute("aria-selected", String(mode === "signup"));
  $("#tab-login").setAttribute("aria-selected", String(mode === "login"));
  $("#auth-submit").textContent = mode === "signup" ? "Create my free account" : "Sign in";
  $("#auth-pass").autocomplete = mode === "signup" ? "new-password" : "current-password";
  $("#pass-hint").hidden = mode !== "signup"; $("#user-hint").hidden = mode !== "signup";
  $("#auth-error").hidden = true;
}
$("#tab-signup").addEventListener("click", () => setAuthMode("signup"));
$("#tab-login").addEventListener("click", () => setAuthMode("login"));

authForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#auth-submit"), err = $("#auth-error");
  btn.disabled = true; err.hidden = true;
  try {
    const local = loadLocal().profile;
    const d = await api(authMode === "signup" ? "/api/signup" : "/api/login", "POST",
      { username: $("#auth-user").value.trim(), password: $("#auth-pass").value, profile: authMode === "signup" ? local : null });
    acct.user = d.username; acct.staleOwner = "";
    if (d.profile) acct.profile = d.profile;
    else if (local) { await api("/api/profile", "PUT", local); acct.profile = local; }  // restore browser copy
    if (acct.profile) saveLocal(acct.profile, acct.user);
    $("#auth-pass").value = "";
    renderAccount(); showView("profile");
    toast(authMode === "signup" ? "Account created. It's free, forever." : `Welcome back, ${acct.user}.`);
  } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  finally { btn.disabled = false; }
});

$("#guest-btn").addEventListener("click", () => showView("profile"));

pform.addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#profile-error"); err.hidden = true;
  try {
    const p = readForm();
    const d = acct.user ? await api("/api/profile", "PUT", p) : await api("/api/profile/check", "POST", p);
    acct.profile = d.profile;
    saveLocal(acct.profile, acct.user || ""); acct.useProfile = true; store.set("cs-use-profile", "1");
    renderAccount(); dlg.close(); toast("Profile saved. Your next search will use it.");
  } catch (ex) { err.textContent = ex.message; err.hidden = false; }
});

$("#signout-btn").addEventListener("click", async () => {
  await api("/api/logout", "POST").catch(() => {});
  clearLocal(); Object.assign(acct, { user: null, profile: null, staleOwner: "" });  // don't leave health info on a shared computer
  renderAccount(); dlg.close(); toast("Signed out. Your profile was removed from this device.");
});

$("#delete-btn").addEventListener("click", async () => {
  if (!confirm("Delete your CaddieSync account and saved profile? This can't be undone.")) return;
  try {
    await api("/api/account", "DELETE");
    clearLocal(); Object.assign(acct, { user: null, profile: null, staleOwner: "" });
    renderAccount(); dlg.close(); toast("Your account and profile were deleted.");
  } catch (ex) { const err = $("#profile-error"); err.textContent = ex.message; err.hidden = false; }
});

$("#clear-guest-btn").addEventListener("click", () => {
  clearLocal(); acct.profile = null; renderAccount(); dlg.close(); toast("Profile removed from this device.");
});

(async function initAccount() {
  const local = loadLocal();
  try {
    const me = await api("/api/me");
    if (me.signed_in) {
      acct.user = me.username;
      if (me.profile) acct.profile = me.profile;
      else if (local.profile) { await api("/api/profile", "PUT", local.profile).catch(() => {}); acct.profile = local.profile; }
      if (acct.profile) saveLocal(acct.profile, acct.user);
    } else if (local.profile) {
      acct.profile = local.profile;
      acct.staleOwner = local.owner;  // had an account on this device but the session ended or the account was reset
    }
  } catch { acct.profile = local.profile; }
  renderAccount();
})();

// Printing a trip plan: open every "What to ask when you call" list so it prints too.
window.addEventListener("beforeprint", () => $$(".call-script").forEach((d) => { d.open = true; }));

document.querySelectorAll(".amen-chip").forEach((b) => b.addEventListener("click", () => {
  const on = b.getAttribute("aria-pressed") !== "true";
  document.querySelectorAll(".amen-chip").forEach((x) => x.setAttribute("aria-pressed", "false"));
  b.setAttribute("aria-pressed", String(on)); amenFilter = on ? b.dataset.amen : ""; drawExplorer();
}));
