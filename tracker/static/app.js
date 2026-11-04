"use strict";
/* Results page. Polls /api/live every 3 s and redraws. No build step, no dependencies. */
(() => {
  const POLL_MS = 3000;
  const STATE_NAMES = {
    AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut",
    DE: "Delaware", DC: "District of Columbia", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois",
    IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland",
    MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana",
    NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York",
    NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania",
    RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah",
    VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
  };
  const AT_LARGE = new Set(["AK", "DE", "ND", "SD", "VT", "WY"]);
  const CALLOUTS = ["VT", "NH", "MA", "RI", "CT", "NJ", "DE", "MD", "DC"];
  const OFFICES = ["senate", "house", "governor"];
  const PARTY_WORD = { D: "Democrat", R: "Republican", I: "Independent" };
  const PARTY_ADJ = { D: "Dem", R: "GOP", I: "Ind" };
  const RATING = { SAFE_D: "Safe D", LIKELY_D: "Likely D", LEAN_D: "Lean D", TOSS_UP: "Toss-up",
    LEAN_R: "Lean R", LIKELY_R: "Likely R", SAFE_R: "Safe R" };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let data = null, shapes = null, byKey = {}, byOffice = {};
  let office = OFFICES.includes(location.hash.slice(1)) ? location.hash.slice(1) : "senate";
  let bgOnly = false, houseView = "map", allCards = false, query = "", sortKey = "race", sortDir = 1;
  let lastOk = 0, failures = 0, tipTarget = null;

  // ------------------------------------------------------------------ formatting
  const fmtET = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
  const fmtETs = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit", second: "2-digit" });
  function etTime(iso, seconds) {
    const d = iso ? new Date(iso) : null;
    if (!d || isNaN(d)) return "";
    const s = (seconds ? fmtETs : fmtET).format(d).replace(" AM", " am").replace(" PM", " pm");
    return seconds ? s : s.replace(":00 ", " ");
  }
  function ago(iso) {
    const s = (Date.now() - new Date(iso)) / 1000;
    if (!(s >= 0)) return "";
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ago`;
  }
  function fmtV(n) {
    n = n || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
    if (n >= 1e5) return Math.round(n / 1e3) + "K";
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
    return String(n);
  }
  const fmtN = (n) => (n || 0).toLocaleString("en-US");
  const pct = (p, d = 0) => (p == null ? "–" : (p * 100).toFixed(d) + "%");
  const pctIn = (e) => (e == null ? "–" : e > 0 && e < 1 ? "<1%" : Math.round(e) + "%");
  const ordinal = (n) => n + (n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th"));
  const lastName = (name) => {
    const parts = String(name || "").split(" ").filter((p) => !/^(Jr\.?|Sr\.?|II|III|IV)$/i.test(p));
    return parts[parts.length - 1] || name;
  };

  // ------------------------------------------------------------------ race helpers
  const partyOf = (c) => (!c ? "I" : c.caucus === "D" || c.caucus === "R" ? c.caucus : c.party === "D" || c.party === "R" ? c.party : "I");
  const letter = (c) => (c.party === "D" || c.party === "R" ? c.party : (c.party || "I").slice(0, 1).toUpperCase());
  const lc = (p) => p.toLowerCase();
  const distNum = (r) => {
    const n = parseInt(String(r.key).split("-")[2], 10);
    return AT_LARGE.has(r.state) || !n ? "AL" : String(n);
  };
  const shortName = (r) => (r.office === "house" ? `${r.state}-${distNum(r).padStart(2, "0")}` : r.state + (r.special ? "*" : ""));
  function raceName(r) {
    const st = STATE_NAMES[r.state] || r.state;
    if (r.office === "senate") return `${st} Senate${r.special ? " (special)" : ""}`;
    if (r.office === "governor") return `${st} Governor`;
    const d = distNum(r);
    return `${st} ${d === "AL" ? "At-large" : "District " + d}`;
  }
  function placePhrase(r) {
    const st = STATE_NAMES[r.state] || r.state;
    if (r.office === "senate") return `the ${st} Senate ${r.special ? "special election" : "race"}`;
    if (r.office === "governor") return `the ${st} governor's race`;
    const d = distNum(r);
    return d === "AL" ? `${st}'s at-large House seat` : `${st}'s ${ordinal(+d)} District`;
  }
  const sortedCands = (r) => [...(r.candidates || [])].sort((a, b) => (b.votes || 0) - (a.votes || 0));

  /** Where a race stands: called, counting (with the leader's margin), polls closed or still open. */
  function standing(r) {
    const cs = sortedCands(r);
    const tv = r.total_votes || cs.reduce((s, c) => s + (c.votes || 0), 0);
    if (r.called) {
      const w = cs.find((c) => c.winner) || cs[0];
      const party = w ? partyOf(w) : r.winner_party === "D" || r.winner_party === "R" ? r.winner_party : "I";
      return { kind: "called", party, step: 4, flip: r.flip === true, lead: w, cs, tv };
    }
    if (tv > 0 && cs.length) {
      const a = cs[0], b = cs[1];
      const margin = (((a.votes || 0) - ((b && b.votes) || 0)) / tv) * 100;
      const step = margin < 1 ? 0 : margin < 5 ? 1 : margin < 10 ? 2 : 3;
      return { kind: "counting", party: partyOf(a), step, margin, lead: a, cs, tv };
    }
    if (r.uncontested && cs.length) return { kind: "called", party: partyOf(cs[0]), step: 4, flip: false, lead: cs[0], cs, tv, unopposed: true };
    const closed = r.poll_close && new Date(r.poll_close) <= Date.now();
    return { kind: closed ? "closed" : "open", cs, tv };
  }
  function fillSvg(st) {
    if (!st) return "var(--none)";
    if (st.kind === "called") return st.flip ? `url(#flip-${st.party})` : `var(--${lc(st.party)}4)`;
    if (st.kind === "counting") return st.step === 0 ? "var(--toss)" : `var(--${lc(st.party)}${st.step})`;
    return st.kind === "closed" ? "url(#hatch)" : "var(--open)";
  }
  function fillCss(st) {
    if (!st) return "var(--none)";
    if (st.kind === "called" && st.flip) return `repeating-linear-gradient(135deg,var(--${lc(st.party)}4) 0 3px,var(--${lc(st.party)}1) 3px 6px)`;
    if (st.kind === "closed") return "repeating-linear-gradient(135deg,var(--closed) 0 3px,var(--hatch) 3px 5px)";
    return fillSvg(st);
  }
  function inkOf(st) {
    if (!st) return "var(--faint)";
    if (st.kind === "called" || (st.kind === "counting" && st.step >= 3)) return "#fff";
    if (st.kind === "counting") return "var(--ink-on-light)";
    return "var(--muted)";
  }
  const YCK = '<i class="yck" title="Projected winner">✓</i>';
  function statusPill(r, st) {
    if (st.kind === "called") {
      if (st.unopposed) return `<span class="pill won">Unopposed ✓</span>`;
      return `<span class="pill won">Projected winner ✓</span>${st.flip ? ` <span class="tag flip">${PARTY_ADJ[st.party]} FLIP</span>` : ""}`;
    }
    if (st.kind === "counting") return `<span class="pill early">Too early to call</span> <span class="muted">${pctIn(r.eevp)} in</span>`;
    if (st.kind === "closed") return `<span class="pill plain">Polls closed · no votes yet</span>`;
    return `<span class="pill plain">Polls close ${etTime(r.poll_close) || "later"} ET</span>`;
  }
  const officeRaces = () => byOffice[office] || [];
  function groupBy(arr, f) { const o = {}; for (const x of arr) (o[f(x)] ||= []).push(x); return o; }
  const raceSort = (a, b) => (STATE_NAMES[a.state] || a.state).localeCompare(STATE_NAMES[b.state] || b.state)
    || (+a.special - +b.special) || (parseInt(distNum(a), 10) || 0) - (parseInt(distNum(b), 10) || 0);

  function index() {
    byKey = {}; byOffice = { senate: [], house: [], governor: [] };
    for (const r of data.races) { byKey[r.key] = r; (byOffice[r.office] ||= []).push(r); }
    for (const k in byOffice) byOffice[k].sort(raceSort);
  }

  /** Latest poll closing per state across every race, grouped by time. */
  function pollClosings() {
    const perState = {};
    for (const r of data.races) if (r.poll_close && (!perState[r.state] || r.poll_close > perState[r.state])) perState[r.state] = r.poll_close;
    const byTime = groupBy(Object.keys(perState), (s) => perState[s]);
    return Object.entries(byTime).map(([at, states]) => ({ at, states: states.sort() })).sort((a, b) => a.at.localeCompare(b.at));
  }

  // ------------------------------------------------------------------ header
  function renderStatus() {
    const age = lastOk ? (Date.now() - lastOk) / 1000 : Infinity;
    const live = $("live");
    live.className = "live" + (age > 60 ? " down" : age > 15 ? " stale" : "");
    live.textContent = age > 60 ? (lastOk ? "OFFLINE" : "CONNECTING") : age > 15 ? "DELAYED" : "LIVE";
    if (!data) return;
    const rs = officeRaces();
    const called = rs.filter((r) => r.called).length;
    const exp = rs.reduce((s, r) => s + (r.expected_votes || 0), 0), got = rs.reduce((s, r) => s + (r.total_votes || 0), 0);
    const next = pollClosings().find((p) => new Date(p.at) > Date.now());
    const label = { senate: "Senate", house: "House", governor: "governor" }[office];
    const bits = [
      `<span>Updated <b>${etTime(data.updated, true)} ET</b></span>`,
      `<span><b>${called}</b> of ${rs.length} ${label} races called</span>`,
      exp ? `<span><b>${Math.round((got / exp) * 100)}%</b> of expected vote in</span>` : "",
      next ? `<span>Next polls close <b>${etTime(next.at)} ET</b> (${next.states.length} state${next.states.length === 1 ? "" : "s"})</span>` : "",
    ];
    $("status").innerHTML = live.outerHTML + bits.join("");
    const src = data.sources || {};
    const chip = (name, s, liveWs) => {
      const cls = !s || s.ok === null ? "off" : s.stale || s.ok === false ? "stale" : "";
      const txt = liveWs && liveWs.ok && !liveWs.stale ? "live" : s && s.age_seconds != null ? `${Math.round(s.age_seconds)}s` : "–";
      return `<span class="src ${cls}" title="${esc(name)} data age">${esc(name)} ${txt}</span>`;
    };
    $("sources").innerHTML = chip("AP votes", src.nyt);
  }

  function renderTabs() {
    for (const o of OFFICES) {
      $(`tab-${o}`).setAttribute("aria-selected", String(o === office));
      const rs = byOffice[o] || [];
      const el = $(`tab-odds-${o}`);
      el.textContent = rs.length ? `${rs.filter((r) => r.called).length}/${rs.length} called` : "";
      el.className = "muted";
    }
    $("view-map").hidden = $("view-seats").hidden = office !== "house";
    $("view-map").setAttribute("aria-pressed", String(houseView === "map"));
    $("view-seats").setAttribute("aria-pressed", String(houseView === "seats"));
    $("bg-only").setAttribute("aria-pressed", String(bgOnly));
  }

  function officeLine(r) {
    if (r.office === "house") return distNum(r) === "AL" ? "At-large district" : `House District ${distNum(r)}`;
    return r.office === "senate" ? (r.special ? "Senate special" : "Senate") : "Governor";
  }
  function renderLatest() {
    const c = data.calls && data.calls[0];
    const r = c && byKey[c.key];
    if (!r) { $("latest").innerHTML = '<span class="muted">No projected winners yet.</span>'; return; }
    const st = standing(r);
    $("latest").innerHTML = `<div class="latest-card" data-key="${esc(r.key)}"><div class="lc-head"><span class="eyebrow">Latest projection · ${esc(etTime(c.at))} ET</span><h3>${esc(STATE_NAMES[r.state] || r.state)}</h3><div class="sub">${esc(officeLine(r))}</div></div><div class="lc-body">${statusPill(r, st)}${candRows(r, st, 2)}</div></div>`;
  }

  // ------------------------------------------------------------------ seats bar
  function renderSeats() {
    const rs = officeRaces();
    const won = { D: 0, R: 0, I: 0 }, lead = { D: 0, R: 0, I: 0 };
    let none = 0;
    for (const r of rs) {
      const st = standing(r);
      if (st.kind === "called") won[st.party]++;
      else if (st.kind === "counting") lead[st.party]++;
      else none++;
    }
    const ch = data.chambers && data.chambers[office];
    const notUp = { D: 0, R: 0, I: 0, ...((ch && ch.not_up) || {}) };
    const total = (ch && ch.total) || rs.length || 1;
    const toWin = ch && ch.to_win;
    const isGov = office === "governor";
    $("seat-d").textContent = notUp.D + won.D;
    $("seat-r").textContent = notUp.R + won.R;
    $("seat-d-label").textContent = isGov ? "Democratic wins" : "Democratic seats";
    $("seat-r-label").textContent = isGov ? "Republican wins" : "Republican seats";
    if (toWin && office === "senate") $("to-win").innerHTML = `<b>${toWin.D}</b>Dems need ${toWin.D}, GOP ${toWin.R} with the VP`;
    else if (toWin) $("to-win").innerHTML = `<b>${toWin.D}</b>to win`;
    else $("to-win").innerHTML = `<b>${rs.length}</b>races`;
    const segs = [
      [notUp.D, "background:var(--d4)", "Dem not up"], [won.D, "background:var(--d3)", "Dem won"], [lead.D, "", "Dem leading", "seg-hatch-d"],
      [notUp.I + won.I, "background:var(--i3)", "Ind seats"], [lead.I, "", "Ind leading", "seg-hatch-i"],
      [none, "background:var(--open)", "No votes yet"],
      [lead.R, "", "GOP leading", "seg-hatch-r"], [won.R, "background:var(--r3)", "GOP won"], [notUp.R, "background:var(--r4)", "GOP not up"],
    ];
    const marker = toWin ? (office === "senate" ? total / 2 : toWin.D) / total : null;
    $("seat-bar").innerHTML = segs.filter((s) => s[0] > 0).map(([n, style, t, cls]) =>
      `<span class="${cls || ""}" style="width:${(n / total) * 100}%;${style}" title="${t}: ${n}"></span>`).join("")
      + (marker != null ? `<i class="marker" style="left:${marker * 100}%"></i>` : "");
    $("seat-bar").setAttribute("aria-label", `Democrats ${notUp.D + won.D} seats, Republicans ${notUp.R + won.R}, ${none} races with no votes yet`);
    const left = [isGov ? null : `${notUp.D} not up`, `${won.D} won`, `${lead.D} leading`].filter(Boolean).join(" · ");
    const right = [`${lead.R} leading`, `${won.R} won`, isGov ? null : `${notUp.R} not up`].filter(Boolean).join(" · ");
    const ind = notUp.I + won.I + lead.I ? ` · Ind ${notUp.I + won.I} won, ${lead.I} leading` : "";
    $("bar-labels").innerHTML = `<span class="d">${left}</span><span>${rs.length} up · ${none} with no votes yet${ind}</span><span class="r">${right}</span>`;
  }

  // ------------------------------------------------------------------ odds + counts strip
  function cell(label, value, sub, extra = "") {
    return `<div class="cell"><div class="eyebrow">${label}</div><div class="v">${value}</div>${sub ? `<div class="s">${sub}</div>` : ""}${extra}</div>`;
  }
  function renderStrip() {
    const rs = officeRaces();
    const n = { calledD: 0, calledR: 0, calledI: 0, leadD: 0, leadR: 0, leadI: 0, flipD: 0, flipR: 0, flipI: 0, close: 0, waiting: 0 };
    for (const r of rs) {
      const st = standing(r);
      if (st.kind === "called") { n["called" + st.party]++; if (st.flip) n["flip" + st.party]++; }
      else if (st.kind === "counting") { n["lead" + st.party]++; if (st.margin < 1) n.close++; }
      else n.waiting++;
    }
    const exp = rs.reduce((s, r) => s + (r.expected_votes || 0), 0), got = rs.reduce((s, r) => s + (r.total_votes || 0), 0);
    const dr = (k) => `<span class="d">D ${n[k + "D"]}</span> · <span class="r">R ${n[k + "R"]}</span>${n[k + "I"] ? ` · <span class="i">I ${n[k + "I"]}</span>` : ""}`;
    $("strip").innerHTML = [
      cell("Projected winners", dr("called"), `${n.calledD + n.calledR + n.calledI} of ${rs.length} races called by AP`),
      cell("Leading in votes", dr("lead"), "Uncalled races with votes counted"),
      cell("Within 1 point", String(n.close), "Closest uncalled races"),
      cell("No votes yet", String(n.waiting), "Polls open, or closed with nothing counted"),
      cell("Expected vote in", exp ? `${Math.round((got / exp) * 100)}%` : "–", `${fmtN(got)} votes counted`),
      cell("Flips so far", dr("flip"), "Seats changing party"),
    ].join("");
  }

  // ------------------------------------------------------------------ map
  function buildMap() {
    const svg = $("map");
    const flip = (p) => `<pattern id="flip-${p}" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="7" height="7" style="fill:var(--${lc(p)}4)"/><rect width="2.5" height="7" style="fill:var(--${lc(p)}1)"/></pattern>`;
    let h = `<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" style="fill:var(--closed)"/><rect width="2" height="6" style="fill:var(--hatch)"/></pattern>${["D", "R", "I"].map(flip).join("")}</defs><g>`;
    for (const [ab, s] of Object.entries(shapes.states)) h += `<path class="st" data-st="${ab}" d="${s.d}"/>`;
    h += "</g><g>";
    for (const [ab, s] of Object.entries(shapes.states)) {
      if (CALLOUTS.includes(ab)) continue;
      h += `<text class="lbl" data-lbl="${ab}" x="${s.x}" y="${s.y}"><tspan class="a" x="${s.x}" dy="-3">${ab}</tspan><tspan class="v" x="${s.x}" dy="12"></tspan></text>`;
    }
    h += "</g><g>";
    CALLOUTS.forEach((ab, i) => {
      const y = 150 + i * 28;
      h += `<g class="co" data-st="${ab}"><rect x="986" y="${y}" width="70" height="22" rx="3"/><text x="993" y="${y + 15}">${ab}</text><text x="1050" y="${y + 15}" text-anchor="end"></text></g>`;
    });
    svg.innerHTML = h + "</g>";
  }
  function stateView(ab, rs) {
    if (!rs) return { st: null, val: "" };
    if (office === "house") {
      let D = 0, R = 0, I = 0, dec = 0, closed = false;
      for (const r of rs) {
        const s = standing(r);
        if (s.kind === "called" || s.kind === "counting") { s.party === "D" ? D++ : s.party === "R" ? R++ : I++; if (s.kind === "called") dec++; }
        else if (s.kind === "closed") closed = true;
      }
      const n = rs.length;
      if (!D && !R && !I) return { st: { kind: closed ? "closed" : "open" }, val: closed ? "–" : etTime(rs[0].poll_close) };
      const top = D > R ? "D" : R > D ? "R" : null;
      const share = Math.max(D, R) / n;
      const st = top ? { kind: dec === n ? "called" : "counting", party: top, step: dec === n ? 4 : share >= 0.8 ? 3 : share >= 0.6 ? 2 : 1 }
        : { kind: "counting", party: "D", step: 0 };
      return { st, val: `${D}–${R}` };
    }
    const r = rs.find((x) => !x.special) || rs[0];
    const st = standing(r);
    let val = "";
    if (st.kind === "called") val = st.unopposed ? "Unopp." : st.tv ? fmtV(st.lead && st.lead.votes) : "";
    else if (st.kind === "counting") val = fmtV(st.lead.votes);
    else if (st.kind === "closed") val = "–";
    else val = etTime(r.poll_close);
    return { st, val, check: st.kind === "called" };
  }
  const labelHtml = (val, check) => (check ? '<tspan class="ck">✓</tspan> ' : "") + esc(val);
  function updateMap() {
    const by = groupBy(officeRaces(), (r) => r.state);
    for (const el of document.querySelectorAll("#map [data-st]")) {
      const ab = el.dataset.st, rs = by[ab];
      const { st, val, check } = stateView(ab, rs);
      const dim = bgOnly && rs && !rs.some((r) => r.battleground);
      const ink = inkOf(st);
      if (el.tagName.toLowerCase() === "g") {
        el.querySelector("rect").style.fill = fillSvg(st);
        const t = el.querySelectorAll("text");
        t[0].style.fill = t[1].style.fill = ink;
        t[1].innerHTML = labelHtml(val, check);
        el.classList.toggle("dim", !!dim);
      } else {
        el.style.fill = fillSvg(st);
        el.classList.toggle("dim", !!dim);
        const lbl = document.querySelector(`[data-lbl="${ab}"]`);
        if (lbl) { lbl.style.fill = ink; lbl.lastElementChild.innerHTML = labelHtml(val, check); lbl.classList.toggle("dim", !!dim); }
      }
    }
    $("map-note").textContent = office === "house"
      ? "Each state shows House seats won or led, Democrats first. Switch to House seats for every district. Hover or tap for details."
      : "Each state shows the leading candidate's vote total; color shows who leads and by how much. Hover or tap a state for the full count.";
  }
  function renderSeatGrid() {
    const show = office === "house" && houseView === "seats";
    $("seatgrid").hidden = !show;
    $("map-box").hidden = show;
    if (!show) return;
    const by = groupBy(officeRaces(), (r) => r.state);
    $("seatgrid").innerHTML = Object.keys(by).sort((a, b) => (STATE_NAMES[a] || a).localeCompare(STATE_NAMES[b] || b)).map((s) =>
      `<div class="sg-state"><h4>${esc(STATE_NAMES[s] || s)} · ${by[s].length}</h4><div class="sg-tiles">${by[s].map((r) => {
        const st = standing(r);
        return `<span class="tile${bgOnly && !r.battleground ? " dim" : ""}" data-key="${esc(r.key)}" style="background:${fillCss(st)};color:${inkOf(st)}">${esc(distNum(r))}</span>`;
      }).join("")}</div></div>`).join("");
  }

  // ------------------------------------------------------------------ status board: every race by vote margin
  const COLS = [["called", "D", "Projected"], [3, "D", "Leads 10+"], [2, "D", "Leads 5–10"], [1, "D", "Leads 1–5"], [0, null, "Within 1"],
    [1, "R", "Leads 1–5"], [2, "R", "Leads 5–10"], [3, "R", "Leads 10+"], ["called", "R", "Projected"]];
  function renderBoard() {
    const cols = COLS.map(([step, party, label]) => ({ step, party, label, races: [] }));
    const indCalled = { step: "called", party: "I", label: "Projected", races: [] }, indLead = { step: 2, party: "I", label: "Leads", races: [] };
    let waiting = 0;
    for (const r of officeRaces()) {
      const st = standing(r);
      if (st.kind !== "called" && st.kind !== "counting") { waiting++; continue; }
      const step = st.kind === "called" ? "called" : st.step;
      if (st.party === "I" && step !== 0) { (step === "called" ? indCalled : indLead).races.push(r); continue; }
      (step === 0 ? cols[4] : cols.find((c) => c.step === step && c.party === st.party)).races.push(r);
    }
    for (const extra of [indLead, indCalled]) if (extra.races.length) cols.splice(5, 0, extra);
    const cap = office === "house" ? 40 : 60;
    $("board").style.setProperty("--cols", cols.length);
    $("board").innerHTML = cols.map((c) => {
      const p = c.party, called = c.step === "called";
      const bg = !p ? "var(--toss)" : `var(--${lc(p)}${called ? 4 : c.step})`;
      const ink = p && (called || c.step === 3) ? "#fff" : "var(--ink-on-light)";
      const head = p ? `<span class="${lc(p)}">${p === "I" ? "Ind " : ""}${c.label}</span>${called ? YCK : ""}` : c.label;
      const chips = c.races.slice(0, cap).map((r) => `<span class="rchip" data-key="${esc(r.key)}" style="background:${bg};color:${ink}">${esc(shortName(r))}</span>`).join("");
      const more = c.races.length > cap ? `<span class="rchip" style="background:var(--panel-2);color:var(--muted)">+${c.races.length - cap}</span>` : "";
      return `<div class="col${p ? "" : " mid"}"><span class="cnt ${p ? lc(p) : ""}">${c.races.length}</span><h3>${head}</h3><div class="chips">${chips}${more}</div></div>`;
    }).join("");
    $("board-note").textContent = `Uncalled races are placed by the leader's margin in the votes counted so far, in percentage points. Projected means AP has called it.${office === "senate" && officeRaces().some((r) => r.special) ? " * Special election." : ""}${waiting ? ` ${waiting} race${waiting === 1 ? " has" : "s have"} no votes counted yet.` : ""}`;
  }

  // ------------------------------------------------------------------ battleground cards
  function candRows(r, st, n = 2) {
    const cs = st.cs.slice(0, n), tv = st.tv;
    return cs.map((c) => {
      const p = partyOf(c), share = tv ? ((c.votes || 0) / tv) * 100 : null;
      const lead = tv && c === st.cs[0];
      return `<div class="cand${lead ? " lead" : ""}"><span class="pbox ${p}">${esc(letter(c))}</span><span class="nm">${esc(lastName(c.name))}${c.incumbent ? ' <span class="muted">(i)</span>' : ""}${c.winner ? YCK : ""}</span><span class="pc">${share == null ? "0%" : share.toFixed(1) + "%"}<span class="vt">${fmtN(c.votes)}</span></span></div>`;
    }).join("");
  }
  function renderCards() {
    let rs = officeRaces().filter((r) => r.battleground);
    let title = "Battlegrounds";
    if (!rs.length) { rs = officeRaces().filter((r) => { const s = standing(r); return s.kind === "counting" && s.margin < 10; }); title = "Close races"; }
    $("bg-h").textContent = `${{ senate: "Senate", house: "House", governor: "Governor" }[office]} ${title.toLowerCase()}`;
    const shown = allCards ? rs : rs.slice(0, 12);
    $("cards").innerHTML = shown.map((r) => {
      const st = standing(r);
      const sub = r.office === "house" ? (distNum(r) === "AL" ? "At-large district" : `House District ${distNum(r)}`) : r.special ? "Special election" : "";
      const mk = st.kind === "called" && r.called_at ? `Called ${etTime(r.called_at)} ET` : st.tv ? `${fmtN(st.tv)} votes counted`
        : r.poll_close ? `Polls ${new Date(r.poll_close) <= Date.now() ? "closed" : "close"} ${etTime(r.poll_close)} ET` : "";
      return `<div class="card" data-key="${esc(r.key)}"><h3>${esc(STATE_NAMES[r.state] || r.state)} ›</h3><div class="sub">${esc(sub)}</div>${statusPill(r, st)}${candRows(r, st, st.cs.length > 2 && st.tv && (st.cs[2].votes || 0) / st.tv > 0.05 ? 3 : 2)}<div class="mk">${esc(mk)}${r.est_votes_outstanding ? ` · ~${fmtV(r.est_votes_outstanding)} left to count` : ""}</div></div>`;
    }).join("") || `<div class="empty">No battleground races for this office.</div>`;
    const more = $("cards-more");
    more.hidden = rs.length <= 12;
    more.textContent = allCards ? "Show fewer races ▴" : `See ${rs.length - 12} more races ▾`;
  }

  // ------------------------------------------------------------------ all races table
  function best(st, test) { return st.cs.find((c) => test(partyOf(c))); }
  function candCell(c, st) {
    if (!c) return `<span class="sm">—</span>`;
    const share = st.tv ? ((c.votes || 0) / st.tv) * 100 : null;
    const lead = st.tv && c === st.cs[0];
    return `<span class="${lead ? "w" : ""}">${esc(lastName(c.name))}${c.winner ? YCK : ""}</span> ${share == null ? "" : share.toFixed(1) + "%"}<div class="sm">${fmtN(c.votes)}</div>`;
  }
  function renderTable() {
    const head = [["race", "Race"], ["dem", "Democrat"], ["rep", "Republican"], ["oth", "Other"], ["in", "% in", "n"], ["status", "Status"]];
    $("tbl").tHead.innerHTML = `<tr>${head.map(([k, t, cls]) => `<th class="${cls || ""}"><button data-sort="${k}">${t}${sortKey === k ? (sortDir > 0 ? " ▴" : " ▾") : ""}</button></th>`).join("")}</tr>`;
    const q = query.trim().toLowerCase();
    let rows = officeRaces().map((r) => ({ r, st: standing(r) }));
    if (q) rows = rows.filter(({ r }) => [raceName(r), shortName(r), r.state, ...(r.candidates || []).map((c) => c.name)].join(" ").toLowerCase().includes(q));
    const margin = ({ r, st }) => {
      const d = best(st, (p) => p === "D"), rr = best(st, (p) => p === "R");
      return st.tv ? (((d && d.votes) || 0) - ((rr && rr.votes) || 0)) / st.tv : 0;
    };
    const rank = { called: 3, counting: 2, closed: 1, open: 0 };
    const keyf = { race: null, in: ({ r }) => r.eevp || 0, dem: margin, rep: (x) => -margin(x), status: ({ st }) => rank[st.kind],
oth: ({ st }) => { const o = best(st, (p) => p === "I"); return o && st.tv ? o.votes / st.tv : 0; } }[sortKey];
    if (keyf) rows.sort((a, b) => (keyf(a) - keyf(b)) * sortDir || raceSort(a.r, b.r));
    else if (sortDir < 0) rows.reverse();
    $("tbl-count").textContent = `${rows.length} race${rows.length === 1 ? "" : "s"}`;
    $("tbl").tBodies[0].innerHTML = rows.map(({ r, st }) => {
      const status = st.kind === "called" ? `<span class="${lc(st.party)}">${PARTY_ADJ[st.party]} win${st.flip ? " (flip)" : ""}</span>${YCK}`
        : st.kind === "counting" ? `<span class="${lc(st.party)}"><i class="lead-dot" style="background:var(--${lc(st.party)}3)"></i>${PARTY_ADJ[st.party]} +${st.margin.toFixed(1)}</span>`
          : st.kind === "closed" ? "Polls closed" : `Polls close ${etTime(r.poll_close)}`;
      return `<tr data-key="${esc(r.key)}"><td><b>${esc(raceName(r))}</b></td><td>${candCell(best(st, (p) => p === "D"), st)}</td><td>${candCell(best(st, (p) => p === "R"), st)}</td><td>${candCell(best(st, (p) => p === "I"), st)}</td><td class="n">${pctIn(r.eevp)}<div class="sm">${fmtV(r.total_votes)}</div></td><td>${status}</td></tr>`;
    }).join("");
  }

  // ------------------------------------------------------------------ feed
  function renderFeed() {
    const items = [];
    for (const c of data.calls || []) {
      const r = byKey[c.key];
      if (!r || !c.at) continue;
      const p = c.party === "D" || c.party === "R" ? c.party : "I";
      const w = (r.candidates || []).find((x) => x.winner);
      const verb = w && w.incumbent ? `wins re-election in ${placePhrase(r)}` : `wins ${placePhrase(r)}`;
      const tag = c.flip === true ? '<span class="tag flip">FLIP</span>' : c.flip === false ? '<span class="tag hold">HOLD</span>' : "";
      items.push({ at: c.at, cls: p, when: `${{ senate: "Senate", house: "House", governor: "Governor" }[r.office]} · Projected winner`, what: `${PARTY_WORD[p]} ${esc(c.winner)} ${esc(verb)}${YCK}${tag}` });
    }
    for (const pc of pollClosings()) if (new Date(pc.at) <= Date.now()) {
      items.push({ at: pc.at, cls: "poll", when: "Polls closed", what: `Polls closed in ${pc.states.length} state${pc.states.length === 1 ? "" : "s"}: ${pc.states.join(", ")}` });
    }
    items.sort((a, b) => b.at.localeCompare(a.at));
    $("feed").innerHTML = items.slice(0, 80).map((i) =>
      `<li class="${i.cls}"><div class="when">${esc(ago(i.at))} · ${esc(etTime(i.at))} ET · ${esc(i.when)}</div><div class="what">${i.what}</div></li>`).join("")
      || `<li><div class="what muted">Calls will appear here as AP makes them.</div></li>`;
  }

  // ------------------------------------------------------------------ tooltip
  function raceTip(r) {
    const st = standing(r), tv = st.tv;
    let h = `<h4>${esc(raceName(r))}</h4>${statusPill(r, st)}`;
    for (const c of st.cs.slice(0, 4)) {
      const p = partyOf(c), share = tv ? (c.votes || 0) / tv : 0;
      h += `<div class="row"><span class="pbox ${p}">${esc(letter(c))}</span><span class="${tv && c === st.cs[0] ? "w" : ""}">${esc(c.name)}${c.incumbent ? ' <span class="muted">(i)</span>' : ""}${c.winner ? YCK : ""}</span><span class="num">${tv ? (share * 100).toFixed(1) + "%" : "–"}</span><span class="vv">${fmtN(c.votes)}</span></div><div class="barline"><span style="width:${(share * 100).toFixed(1)}%;background:var(--${lc(p)}3)"></span></div>`;
    }
    const foot = [];
    if (tv) foot.push(`${pctIn(r.eevp)} of expected vote in · ${fmtN(r.total_votes)} counted`);
    if (r.est_votes_outstanding) foot.push(`About ${fmtN(r.est_votes_outstanding)} votes left to count`);
    if (r.called && r.called_at) foot.push(`AP called it at ${etTime(r.called_at)} ET`);
    if (RATING[r.rating]) foot.push(`Pre-election rating: ${RATING[r.rating]}`);
    if (r.poll_close) foot.push(`Polls ${new Date(r.poll_close) <= Date.now() ? "closed" : "close"} ${etTime(r.poll_close)} ET`);
    return h + `<div class="foot">${foot.map((f) => `<span>${esc(f)}</span>`).join("")}</div>`;
  }
  function stateTip(ab) {
    const rs = officeRaces().filter((r) => r.state === ab);
    if (!rs.length) return `<h4>${esc(STATE_NAMES[ab] || ab)}</h4><div class="foot"><span>No ${office === "governor" ? "governor" : office === "senate" ? "Senate" : "House"} race this year.</span></div>`;
    if (office !== "house") return rs.map(raceTip).join('<hr style="border:0;border-top:1px solid var(--line);margin:10px 0">');
    const rows = rs.map((r) => {
      const st = standing(r);
      const who = st.lead && (st.kind === "called" || st.kind === "counting")
        ? `<span class="${lc(st.party)}">${esc(lastName(st.lead.name))}${st.kind === "called" ? YCK : ""}</span>${st.kind === "counting" ? ` +${st.margin.toFixed(1)}` : ""}`
        : `<span class="muted">${st.kind === "closed" ? "no votes yet" : "polls open"}</span>`;
      return `<div class="row" style="grid-template-columns:44px 1fr auto"><b>${esc(shortName(r))}</b><span>${who}</span><span class="vv">${pctIn(r.eevp)} in</span></div>`;
    }).join("");
    return `<h4>${esc(STATE_NAMES[ab] || ab)} House</h4>${rows}`;
  }
  function showTip(el, x, y) {
    const tip = $("tip");
    tipTarget = el;
    tip.innerHTML = el.dataset.key ? (byKey[el.dataset.key] ? raceTip(byKey[el.dataset.key]) : "") : stateTip(el.dataset.st);
    tip.hidden = false;
    if (x != null) { tip.dataset.x = x; tip.dataset.y = y; }
    const w = tip.offsetWidth, h = tip.offsetHeight, px = +tip.dataset.x, py = +tip.dataset.y;
    let left = px + 16, top = py + 16;
    if (left + w > innerWidth - 8) left = Math.max(8, px - w - 16);
    if (top + h > innerHeight - 8) top = Math.max(8, innerHeight - h - 8);
    tip.style.left = left + "px";
    tip.style.top = top + "px";
    document.querySelectorAll("#map .hl").forEach((n) => n.classList.remove("hl"));
    if (el.classList.contains("st")) el.classList.add("hl");
  }
  function hideTip() {
    $("tip").hidden = true; tipTarget = null;
    document.querySelectorAll("#map .hl").forEach((n) => n.classList.remove("hl"));
  }
  const tipEl = (t) => t && t.closest && t.closest("[data-key], #map [data-st]");
  document.addEventListener("pointermove", (e) => {
    if (e.pointerType === "touch") return;
    const el = tipEl(e.target);
    el ? showTip(el, e.clientX, e.clientY) : tipTarget && hideTip();
  });
  document.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch") return;
    const el = tipEl(e.target);
    el ? showTip(el, e.clientX, e.clientY) : hideTip();
  });
  addEventListener("scroll", () => tipTarget && hideTip(), { passive: true });

  // ------------------------------------------------------------------ controls
  document.addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.office) { office = t.dataset.office; allCards = false; history.replaceState(null, "", "#" + office); render(); }
    else if (t.id === "bg-only") { bgOnly = !bgOnly; render(); }
    else if (t.id === "view-map" || t.id === "view-seats") { houseView = t.id === "view-map" ? "map" : "seats"; render(); }
    else if (t.id === "cards-more") { allCards = !allCards; renderCards(); }
    else if (t.dataset.sort) { sortDir = sortKey === t.dataset.sort ? -sortDir : 1; sortKey = t.dataset.sort; renderTable(); }
  });
  addEventListener("hashchange", () => { const h = location.hash.slice(1); if (OFFICES.includes(h)) { office = h; render(); } });
  $("search").addEventListener("input", (e) => { query = e.target.value; renderTable(); });

  // ------------------------------------------------------------------ loop
  function render() {
    renderStatus();
    if (!data) return;
    renderTabs(); renderLatest(); renderSeats(); renderStrip();
    if (shapes) updateMap();
    renderSeatGrid(); renderBoard(); renderCards(); renderTable(); renderFeed();
    if (tipTarget) {
      const sel = tipTarget.dataset.key ? `[data-key="${CSS.escape(tipTarget.dataset.key)}"]` : `#map [data-st="${tipTarget.dataset.st}"]`;
      const el = document.querySelector(sel);
      el ? showTip(el) : hideTip();
    }
  }
  async function tick() {
    try {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 10000);
      const res = await fetch("/api/live", { cache: "no-store", signal: ctl.signal });
      clearTimeout(timer);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      lastOk = Date.now(); failures = 0;
      index(); render();
    } catch (err) {
      failures++;
      renderStatus();
    }
    setTimeout(tick, failures ? Math.min(POLL_MS * 2 ** failures, 20000) : POLL_MS);
  }
  setInterval(() => { renderStatus(); if (data) renderFeed(); }, 5000);
  fetch("/static/us-states.json").then((r) => r.json()).then((j) => { shapes = j; buildMap(); if (data) updateMap(); }).catch(() => {
    $("map-note").textContent = "The map didn't load. Reload the page to try again.";
  });
  tick();
})();
