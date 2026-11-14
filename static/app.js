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
    ${s.used_profile ? `<p class="note-small">Used your saved profile${s.from_profile.length ? ` (kept: ${esc(s.from_profile.join("; "))})` : ""}.</p>` : ""}
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
    const msg = typeof d.detail === "string" ? d.detail : Array.isArray(d.detail) ? "Please check the highlighted fields." : (d.error || "Something went wrong.");
    throw new Error(msg);
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
    bar.hidden = false;
    bar.className = "profile-cta";
    bar.innerHTML = `<span>Save your group and needs once and skip retyping them. Free, with no email needed.</span>
      <button type="button" class="link-btn" data-open-profile>Create a free profile</button>`;
  } else {
    const p = acct.profile, chips = needSummary(p);
    const bits = [`${p.usual_players} player${p.usual_players === 1 ? "" : "s"}`, p.usual_budget ? `up to $${p.usual_budget}` : null,
      p.preferred_time !== "any" ? WINDOW[p.preferred_time] : null].filter(Boolean).join(" · ");
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

function fillForm(p) {
  const f = pform.elements;
  const d = p || { display_name: "", home_town: "", usual_players: 1, usual_budget: null, preferred_time: "any", holes: "either",
    walking_ability: "full", terrain: "any", forced_carries: "any", bunkers: "any", cart_near_greens: false, adaptive_cart: false,
    shorter_course: false, skill: "unknown", notes: "" };
  f.display_name.value = d.display_name; f.home_town.value = d.home_town; f.usual_budget.value = d.usual_budget ?? "";
  f.preferred_time.value = d.preferred_time; f.skill.value = d.skill; f.notes.value = d.notes;
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
    usual_players: Number(radio("usual_players") || 1), usual_budget: f.usual_budget.value === "" ? null : Number(f.usual_budget.value),
    preferred_time: f.preferred_time.value, holes: radio("holes") || "either",
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

function openDialog() {
  showView(acct.user || acct.profile ? "profile" : "auth");
  dlg.showModal();
}
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
    if (acct.user) { const d = await api("/api/profile", "PUT", p); acct.profile = d.profile; }
    else { const d = await api("/api/profile/check", "POST", p); acct.profile = d.profile; }
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
