// Ads marketplace: "Publish" (monetize my channels) and "Advertise" (run campaigns).
import { h, icon, fmt, short, skeleton, sheet, toast, confetti, compressImage, ago, countUp } from "../ui.js";
import { t } from "../i18n.js";
import { api } from "../api.js";
import { S, fail, patchUser } from "../state.js";
import { haptic, openTelegramLink, openLink, mainButton, confirmDialog } from "../tg.js";

const money = (n) => fmt(n) + " GRAM";
const pct = (n) => (Number(n) || 0).toFixed(2) + "%";
const dash = (n, f = fmt) => (n == null ? "—" : f(n));
const TZ = () => -new Date().getTimezoneOffset();
const utc = () => "UTC" + (TZ() >= 0 ? "+" : "") + (TZ() / 60);
const share = () => Math.round((1 - ((S.config.ads || {}).commission ?? 0.2)) * 100);
const STATUS_TAG = { active: "ok", paused: "warn", pending_review: "warn", rejected: "bad", stopped: "bad", finished: "info", posted: "info", completed: "ok", failed: "bad", removed_early: "bad" };
const tag = (s) => h("span", { class: "tag " + (STATUS_TAG[s] || "info") }, t("ads.st_" + s));
const stat = (v, k, ic, g) => h("div", { class: "stat" }, ic && h("div", { class: "ico " + (g || "g5") }, icon(ic, 16)), h("div", { class: "v num" }, v), h("div", { class: "k" }, k));

// compact SVG bar chart: one metric per day
function chart(series, key, label) {
  const W = 320, H = 110, pad = 6, vals = series.map((d) => Number(d[key]) || 0), max = Math.max(1, ...vals);
  const bw = (W - pad * 2) / Math.max(1, vals.length);
  const bars = vals.map((v, i) => `<rect x="${(pad + i * bw + 1.5).toFixed(1)}" y="${(H - 18 - (v / max) * (H - 30)).toFixed(1)}" width="${Math.max(2, bw - 3).toFixed(1)}" height="${Math.max(1, (v / max) * (H - 30)).toFixed(1)}" rx="3" fill="url(#g${key})" opacity="${v ? 1 : .25}"/>`).join("");
  const el = h("div", { class: "card flat" }, h("div", { class: "row between" }, h("div", { class: "h3" }, label), h("span", { class: "small muted num" }, fmt(vals.reduce((a, b) => a + b, 0)))),
    h("div", { html: `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${label}"><defs><linearGradient id="g${key}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="var(--a1,#7c5cff)"/><stop offset="1" stop-color="var(--a2,#22d3ee)"/></linearGradient></defs>${bars}<text x="${pad}" y="${H - 3}" font-size="9" fill="currentColor" opacity=".5">${(series[0] || {}).date || ""}</text><text x="${W - pad}" y="${H - 3}" font-size="9" text-anchor="end" fill="currentColor" opacity=".5">${(series[series.length - 1] || {}).date || ""}</text></svg>` }));
  return el;
}
const seriesBlock = (series) => {
  let key = "views"; const holder = h("div"), chips = h("div", { class: "chips", style: { marginBottom: "8px" } });
  const draw = () => { chips.replaceChildren(...[["views", t("ads.views")], ["clicks", t("ads.clicks")]].map(([k, l]) => h("button", { class: "chip" + (k === key ? " on" : ""), onclick: () => { key = k; haptic.select(); draw(); } }, l))); holder.replaceChildren(chart(series, key, key === "views" ? t("ads.views") : t("ads.clicks"))); };
  draw(); return h("div", null, chips, holder);
};

// ============================ main tab ============================
export async function render({ root, params, push }) {
  let seg = params.seg || (localStorage.getItem("bg:adseg") || "publish");
  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("tab.ads"))));
  const tabs = h("div", { class: "seg", style: { marginBottom: "14px" } }), body = h("div");
  root.append(tabs, body);
  const draw = () => {
    tabs.replaceChildren(...[["publish", t("ads.publish")], ["advertise", t("ads.advertise")]].map(([k, l]) => h("button", { class: k === seg ? "on" : "", onclick: () => { if (seg === k) return; seg = k; localStorage.setItem("bg:adseg", k); haptic.select(); draw(); } }, l)));
    body.replaceChildren(); (seg === "publish" ? publishTab : advertiseTab)(body, push);
  };
  draw();
}

// ---------------- Publish ----------------
async function publishTab(root, push) {
  root.append(...skeleton(3, 90));
  let r;
  try { r = await api.adChannels(); } catch (e) { root.replaceChildren(h("div", { class: "empty" }, t("err.generic"))); return; }
  const tot = r.items.reduce((a, c) => ({ earned: a.earned + c.earned, views: a.views + c.stats.views, clicks: a.clicks + c.stats.clicks, live: a.live + c.stats.live }), { earned: 0, views: 0, clicks: 0, live: 0 });
  const kids = [];
  kids.push(h("div", { class: "card", style: { background: "linear-gradient(140deg, rgba(34,211,238,.25), rgba(124,92,255,.3))" } },
    h("div", { class: "small muted", style: { fontWeight: 700 } }, t("ads.pub_earned")), h("div", { class: "h1 gold-text num", style: { fontSize: "30px" } }, money(tot.earned)),
    h("div", { class: "grid3", style: { marginTop: "12px" } }, stat(short(tot.views), t("ads.views")), stat(fmt(tot.clicks), t("ads.clicks")), stat(fmt(tot.live), t("ads.live")))));
  kids.push(h("button", { class: "btn shine", style: { margin: "14px 0" }, onclick: () => { haptic.tap(); push("adaddchannel"); } }, icon("plus", 19), t("ads.add_channel")));
  if (!r.items.length) kids.push(h("div", { class: "card center", style: { padding: "26px 18px" } }, h("div", { style: { fontSize: "46px" } }, "📣"), h("div", { class: "h2" }, t("ads.pub_empty")), h("div", { class: "muted small", style: { marginTop: "6px" } }, t("ads.pub_empty_sub", { p: Math.round((1 - r.rules.commission) * 100) }))));
  r.items.forEach((c) => kids.push(h("div", { class: "card tap flat", style: { marginBottom: "10px" }, onclick: () => push("adchannel", { id: c.id }) },
    h("div", { class: "row" }, h("div", { class: "grow" }, h("div", { class: "h3" }, c.title), h("div", { class: "small muted" }, "@" + c.username + " · " + t("ads.members", { n: fmt(c.members) }))), tag(c.status)),
    h("div", { class: "grid3", style: { marginTop: "10px" } }, stat(fmt(c.slots.length), t("ads.posts_day")), stat(short(c.stats.views), t("ads.views")), stat(short(c.earned), t("ads.earned"))),
    c.pause_reason && h("div", { class: "callout warn", style: { marginTop: "8px" } }, icon("info", 16), h("div", null, c.pause_reason)))));
  root.replaceChildren(...kids);
}

// ---------------- Advertise ----------------
async function advertiseTab(root, push) {
  root.append(...skeleton(3, 90));
  let r;
  try { r = await api.adCampaigns(); } catch (e) { root.replaceChildren(h("div", { class: "empty" }, t("err.generic"))); return; }
  const s = r.totals, kids = [];
  kids.push(h("div", { class: "card", style: { background: "linear-gradient(140deg, rgba(124,92,255,.35), rgba(236,72,153,.2))" } },
    h("div", { class: "small muted", style: { fontWeight: 700 } }, t("ads.adv_spent")), h("div", { class: "h1 gold-text num", style: { fontSize: "30px" } }, money(s.spent)),
    h("div", { class: "grid3", style: { marginTop: "12px" } }, stat(short(s.views), t("ads.impressions")), stat(fmt(s.clicks), t("ads.clicks")), stat(dash(s.cpc, short), t("ads.cpc")))));
  kids.push(h("button", { class: "btn shine", style: { margin: "14px 0" }, onclick: () => { haptic.tap(); push("adcreate"); } }, icon("rocket", 19), t("ads.new_campaign")));
  if (!r.items.length) kids.push(h("div", { class: "card center", style: { padding: "26px 18px" } }, h("div", { style: { fontSize: "46px" } }, "🚀"), h("div", { class: "h2" }, t("ads.adv_empty")), h("div", { class: "muted small", style: { marginTop: "6px" } }, t("ads.adv_empty_sub"))));
  r.items.forEach((c) => {
    const used = c.budget ? Math.min(100, ((c.budget - c.remaining - c.refunded) / c.budget) * 100) : 0;
    kids.push(h("div", { class: "card tap flat", style: { marginBottom: "10px" }, onclick: () => push("adcampaign", { id: c.id }) },
      h("div", { class: "row" }, h("div", { class: "grow" }, h("div", { class: "h3" }, c.title), h("div", { class: "small muted" }, t("ads.posts_n", { n: c.stats.posts }))), tag(c.status)),
      h("div", { class: "bar", style: { margin: "10px 0 6px" } }, h("i", { style: { width: used + "%" } })),
      h("div", { class: "row between tiny muted" }, h("span", { class: "num" }, short(c.spent) + " / " + short(c.budget)), h("span", { class: "num" }, short(c.stats.views) + " " + t("ads.views") + " · " + fmt(c.stats.clicks) + " " + t("ads.clicks")))));
  });
  root.replaceChildren(...kids);
}

// ============================ add channel ============================
export async function addChannel({ root, replace, go }) {
  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("ads.add_channel"))));
  let r; root.append(...skeleton(2, 90));
  try { r = await api.adChannels(); } catch (e) { root.replaceChildren(h("div", { class: "empty" }, t("err.generic"))); return; }
  const st = { busy: false };
  const done = (ch) => { confetti(40); haptic.ok(); toast(t("ads.channel_added"), "ok"); replace("adchannel", { id: ch.id, setup: true }); };
  async function add(payload, btn) {
    if (st.busy) return; st.busy = true; btn && (btn.disabled = true);
    try { const x = await api.adAddChannel(payload); done(x.channel); } catch (e) { fail(e); st.busy = false; btn && (btn.disabled = false); }
  }
  const input = h("input", { class: "input", placeholder: "@mychannel", autocapitalize: "none", autocomplete: "off" });
  const manual = h("button", { class: "btn", onclick: () => { const v = input.value.trim(); if (v) add({ username: v }, manual); } }, icon("check", 19), t("ads.verify_add"));
  const cands = r.candidates || [];
  const kids = [
    h("div", { class: "card flat col" },
      h("div", { class: "row" }, h("div", { class: "ico g1" }, h("span", null, "1")), h("div", { class: "grow" }, h("div", { class: "h3" }, t("ads.step_bot")), h("div", { class: "small muted" }, t("ads.step_bot_sub")))),
      h("button", { class: "btn sec", onclick: () => { haptic.tap(); openTelegramLink(r.rules.add_link); } }, icon("bot", 19), t("ads.add_bot_btn"))),
    h("div", { class: "card flat col", style: { marginTop: "10px" } },
      h("div", { class: "row" }, h("div", { class: "ico g2" }, h("span", null, "2")), h("div", { class: "grow" }, h("div", { class: "h3" }, t("ads.step_pick")), h("div", { class: "small muted" }, t("ads.step_pick_sub")))),
      ...cands.map((c) => { const b = h("button", { class: "lang", onclick: () => add({ chat_id: c.chat_id }, b) }, h("span", null, c.title, c.username ? " · @" + c.username : ""), icon("plus", 18)); return b; }),
      h("div", { class: "field" }, h("label", null, t("ads.or_username")), input), manual),
    h("div", { class: "callout", style: { marginTop: "10px" } }, icon("shield", 18), h("div", null, t("ads.requirements", { n: fmt(r.rules.min_members) })))];
  root.replaceChildren(root.firstChild, ...kids);
  // refresh candidates when the user returns from the Telegram chat picker
  const onVis = () => { if (!document.hidden) api.adChannels().then((x) => { if ((x.candidates || []).length !== cands.length) replace("adaddchannel"); }).catch(() => {}); };
  document.addEventListener("visibilitychange", onVis);
  return () => document.removeEventListener("visibilitychange", onVis);
}

// ============================ channel detail ============================
export async function channel({ root, params, back, replace }) {
  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("ads.channel"))), ...skeleton(3, 90));
  let d;
  try { d = await api.adChannelStats(params.id); } catch (e) { root.replaceChildren(h("div", { class: "empty" }, t("err.generic"))); return; }
  const c = d.channel, s = c.stats, kids = [];
  kids.push(h("div", { class: "pagehead" }, h("div", { class: "grow" }, h("div", { class: "h1", style: { fontSize: "21px" } }, c.title), h("div", { class: "small muted" }, "@" + c.username + " · " + t("ads.members", { n: fmt(c.members) }))), tag(c.status)));
  kids.push(h("div", { class: "card", style: { background: "linear-gradient(140deg, rgba(34,211,238,.25), rgba(124,92,255,.3))" } },
    h("div", { class: "small muted", style: { fontWeight: 700 } }, t("ads.pub_earned")), h("div", { class: "h1 gold-text num", style: { fontSize: "30px" } }, money(c.earned)),
    s.pending > 0 && h("div", { class: "small muted", style: { marginTop: "4px" } }, t("ads.pending") + ": ", h("b", { class: "num" }, money(s.pending)))));
  kids.push(h("div", { class: "callout", style: { marginTop: "10px" } }, icon("shield", 18), h("div", null, t("ads.earn_note", { p: share() }))));
  kids.push(h("div", { class: "grid2 mt" }, stat(fmt(s.views), t("ads.impressions"), "eye", "g5"), stat(fmt(s.clicks), t("ads.clicks"), "trend", "g2"),
    stat(pct(s.ctr), t("ads.ctr"), "bolt", "g4"), stat(fmt(s.bot_starts), t("ads.bot_starts"), "bot", "g1"),
    stat(dash(s.rpm, short), t("ads.rpm"), "star", "g3"), stat(dash(s.epc, short), t("ads.epc"), "link", "g5")));
  kids.push(h("div", { class: "section" }, seriesBlock(d.series)));
  if (!S.config.ads || S.config.ads.view_sync === false) { /* views may be unavailable */ }
  kids.push(h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("ads.slots_title"))),
    h("div", { class: "card flat" }, h("div", { class: "chips" }, c.slots.map((x) => h("span", { class: "chip on", style: { pointerEvents: "none" } }, x))),
      h("div", { class: "tiny muted", style: { marginTop: "8px" } }, t("ads.slots_sub") + " (" + utc() + ")"),
      h("div", { class: "list", style: { marginTop: "8px" } }, row(t("ads.slots_count", { n: c.slots.length }), ""), row(t("ads.lifetime"), c.lifetime + t("ads.h")), row(t("ads.strikes"), c.strikes + " / " + ((S.config.ads || {}).max_strikes || 3)))),
    h("div", { class: "grid2", style: { marginTop: "10px" } },
      h("button", { class: "btn sec", onclick: () => settingsSheet(c, (n) => replace("adchannel", { id: c.id })) }, icon("sliders", 18), t("ads.edit")),
      h("button", { class: "btn sec", onclick: async () => { try { await api.adChannelAction(c.id, c.status === "active" ? "pause" : "resume"); haptic.ok(); replace("adchannel", { id: c.id }); } catch (e) { fail(e); } } }, icon(c.status === "active" ? "clock" : "bolt", 18), c.status === "active" ? t("ads.pause") : t("ads.resume"))),
    h("button", { class: "btn sec", style: { marginTop: "10px", color: "var(--bad,#ff5470)" }, onclick: async () => { if (!(await confirmDialog(t("ads.remove_q")))) return; try { await api.adChannelAction(c.id, "remove"); haptic.ok(); back(); } catch (e) { fail(e); } } }, icon("x", 18), t("ads.remove"))));
  kids.push(h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("ads.recent"))),
    d.recent.length ? h("div", { class: "card flat", style: { padding: "2px 14px" } }, h("div", { class: "list" }, ...d.recent.map((p) => h("div", { class: "item" },
      h("div", { class: "grow" }, h("div", { class: "h3", style: { fontSize: "14px" } }, p.campaign_title || "—"), h("div", { class: "tiny muted" }, (p.posted_at ? ago(p.posted_at, t) : "—") + " · " + fmt(p.views) + " " + t("ads.views") + " · " + fmt(p.clicks) + " " + t("ads.clicks"))),
      h("div", { class: "col", style: { alignItems: "flex-end", gap: "4px" } }, h("div", { class: "amt num" }, "+" + short(p.earn)), tag(p.status)))))) : h("div", { class: "empty small" }, t("ads.no_posts"))));
  root.replaceChildren(...kids);
  if (params.setup) setTimeout(() => settingsSheet(c, () => replace("adchannel", { id: c.id })), 250);
}
const row = (k, v) => h("div", { class: "item" }, h("div", { class: "grow muted" }, k), h("b", { class: "num" }, v));

function settingsSheet(c, onSaved) {
  sheet((s) => {
    const rules = S.config.ads || {}, max = rules.max_slots || 12, gap = rules.slot_gap || 30;
    const st = { slots: [...c.slots], life: c.lifetime, auto: c.auto_delete, busy: false };
    const toMin = (x) => +x.slice(0, 2) * 60 + +x.slice(3);
    const okGap = (arr) => { const m = arr.map(toMin).sort((a, b) => a - b); return m.length < 2 || m.every((v, i) => (i ? v - m[i - 1] : m[0] + 1440 - m[m.length - 1]) >= gap); };
    const list = h("div", { class: "chips" }), count = h("div", { class: "tiny muted" });
    const draw = () => { list.replaceChildren(...st.slots.map((x) => h("button", { class: "chip on", onclick: () => { if (st.slots.length <= 1) return; st.slots = st.slots.filter((y) => y !== x); haptic.select(); draw(); } }, x + "  ×"))); count.textContent = t("ads.slots_count", { n: st.slots.length }) + " · " + utc(); };
    const tin = h("input", { type: "time", class: "input", value: "12:00", style: { flex: 1 } });
    const add = h("button", { class: "btn sec sm", onclick: () => {
      const v = tin.value; if (!/^\d\d:\d\d$/.test(v) || st.slots.includes(v)) return;
      const n = [...st.slots, v].sort(); if (n.length > max || !okGap(n)) return toast(t("ads.slots_hint", { n: max, m: gap }), "err");
      st.slots = n; haptic.select(); draw();
    } }, icon("plus", 18), t("ads.slot_add"));
    const life = h("div", { class: "chips" }, (rules.lifetimes || [1, 3, 6, 12, 24]).map((v) => { const b = h("button", { class: "chip" + (st.life === v ? " on" : ""), onclick: () => { st.life = v; haptic.select(); life.querySelectorAll(".chip").forEach((x) => x.classList.toggle("on", x === b)); } }, v + t("ads.h")); return b; }));
    const save = h("button", { class: "btn shine", onclick: async () => {
      if (st.busy) return; st.busy = true; save.disabled = true;
      try { await api.adChannelUpdate(c.id, { slots: st.slots, tz_offset: TZ(), lifetime: st.life, auto_delete: st.auto }); haptic.ok(); toast(t("ads.saved"), "ok"); s.close(); onSaved(); }
      catch (e) { fail(e); st.busy = false; save.disabled = false; }
    } }, icon("check", 19), t("ads.save"));
    draw();
    s.render(h("div", { class: "h2", style: { marginBottom: "4px" } }, t("ads.slots_title")), h("div", { class: "small muted", style: { marginBottom: "12px" } }, t("ads.slots_sub")), h("div", { class: "col" },
      list, count,
      h("div", { class: "row", style: { gap: "8px" } }, tin, add),
      h("div", { class: "tiny muted" }, t("ads.slots_hint", { n: max, m: gap })),
      h("div", { class: "field" }, h("label", null, t("ads.lifetime")), life, h("div", { class: "hint" }, t("ads.lifetime_hint"))),
      h("div", { class: "callout" }, icon("shield", 18), h("div", null, t("ads.pub_note"))), save));
  });
}

// ============================ create campaign ============================
export async function create({ root, replace }) {
  const cfg = S.config.ads || {}, models = cfg.models || ["cpc"];
  const bounds = (m) => cfg[m] || { min: 1, default: 1, max: 1e9 };
  const st = { title: "", text: "", button_text: "", button_url: "", pricing: models.includes("cpm") ? "cpm" : "cpc", bid: 0, budget: 500000, posts_per_day: 6, days: 7, min_members: 0, blob: null, kind: null, preview: null, busy: false, est: null };
  st.bid = bounds(st.pricing).default;
  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("ads.new_campaign"))));
  const wrap = h("div", { class: "col gap-lg" }); root.append(wrap);
  const estBox = h("div", { class: "card flat" });
  let timer = null;
  const refreshEst = () => { clearTimeout(timer); timer = setTimeout(async () => {
    try { st.est = await api.adEstimate({ pricing: st.pricing, bid: st.bid, budget: st.budget, days: st.days, min_members: st.min_members }); } catch (_) { st.est = null; }
    drawEst(); }, 350); };
  const drawEst = () => {
    const e = st.est;
    if (!e) return estBox.replaceChildren(h("div", { class: "small muted" }, "…"));
    const com = Math.round((e.commission ?? cfg.commission ?? 0.2) * 100);
    estBox.replaceChildren(h("div", { class: "h3", style: { marginBottom: "8px" } }, t("ads.estimate")),
      e.channels ? h("div", { class: "list" },
        row(t("ads.est_channels"), fmt(e.channels)), row(t("ads.est_slots"), fmt(e.slots_per_day)), row(t("ads.est_audience"), fmt(e.audience)),
        e.views != null && row(t("ads.est_views"), fmt(e.views)), e.clicks != null && row(t("ads.est_clicks"), fmt(e.clicks)),
        row(t("ads.est_publishers", { n: 100 - com }), money(e.publisher_share)), row(t("ads.est_fee", { n: com }), money(e.platform_fee))) : h("div", { class: "callout warn" }, icon("info", 16), h("div", null, t("ads.est_none"))),
      h("div", { class: "tiny muted", style: { marginTop: "8px" } }, t("ads.escrow_note")));
  };
  const valid = () => { const b = bounds(st.pricing); return st.title.trim().length >= 2 && st.text.trim().length >= 10 && st.button_text.trim().length >= 2 && /^https?:\/\//i.test(st.button_url.trim()) && st.bid >= b.min && st.bid <= b.max && st.budget >= Math.max(cfg.min_budget || 0, st.bid * (st.pricing === "cpm" ? 1 : 3)) && st.budget <= S.user.balance; };
  const sync = () => mainButton.set({ text: t("ads.launch") + " · " + short(st.budget), disabled: !valid() || st.busy, loading: st.busy, onClick: submit });
  const field = (label, el, hint) => h("div", { class: "field" }, h("label", null, label), el, hint && h("div", { class: "hint" }, hint));
  const nin = (key) => h("input", { class: "input num", inputmode: "numeric", value: st[key], oninput: (e) => { st[key] = parseInt(e.target.value.replace(/\D/g, "")) || 0; refreshEst(); sync(); } });
  const media = () => st.preview ? (st.kind === "video" ? h("video", { src: st.preview, muted: true, loop: true, autoplay: true, playsinline: true, style: { width: "100%", borderRadius: "12px", margin: "6px 0", maxHeight: "260px", background: "#000" } }) : h("img", { src: st.preview, alt: "", style: { width: "100%", borderRadius: "12px", margin: "6px 0" } })) : null;
  const preview = h("div", { class: "card flat", style: { whiteSpace: "pre-wrap" } });
  const upPrev = () => preview.replaceChildren(h("div", { class: "tiny muted" }, t("ads.preview")), media(), h("div", null, st.text || "…"), h("div", { class: "btn sm sec", style: { marginTop: "8px", pointerEvents: "none" } }, st.button_text || "…"), h("div", { class: "tiny muted", style: { marginTop: "6px" } }, "📣 " + t("ads.sponsored") + " · @" + S.config.bot_username));
  const drop = () => h("label", { class: "drop" + (st.preview ? " has" : "") }, st.preview ? (st.kind === "video" ? icon("check", 26) : h("img", { src: st.preview, alt: "" })) : [icon("image", 26), h("div", { class: "h3", style: { marginTop: "6px" } }, t("ads.media")), h("div", { class: "small" }, t("ads.media_hint", { n: cfg.video_mb || 10 }))],
    st.preview && st.kind === "video" && h("div", { class: "small" }, t("ads.media")),
    h("input", { type: "file", accept: "image/*,video/mp4,video/quicktime", class: "hide", onchange: async (e) => {
      const f = e.target.files[0]; if (!f) return;
      try {
        if (f.type.startsWith("video/")) {
          if (f.size > (cfg.video_mb || 10) * 1048576) return toast(t("ads.video_big", { n: cfg.video_mb || 10 }), "err");
          st.blob = f; st.kind = "video";
        } else { st.blob = await compressImage(f); st.kind = "photo"; }
        st.preview = URL.createObjectURL(st.blob); haptic.ok(); draw();
      } catch (_) { toast(t("err.bad_image"), "err"); }
    } }));
  const model = () => h("div", { class: "col" }, models.map((m) => h("button", { class: "lang" + (st.pricing === m ? " on" : ""), onclick: () => { if (st.pricing === m) return; st.pricing = m; st.bid = bounds(m).default; haptic.select(); draw(); refreshEst(); } },
    h("span", { style: { textAlign: "start" } }, h("b", null, t("ads." + m + "_name")), h("div", { class: "small muted" }, t("ads." + m + "_sub"))))));
  function draw() {
    const b = bounds(st.pricing);
    wrap.replaceChildren(
      field(t("ads.c_title"), h("input", { class: "input", value: st.title, maxlength: 120, oninput: (e) => { st.title = e.target.value; sync(); } }), t("ads.c_title_hint")),
      field(t("ads.c_text"), h("textarea", { class: "input", rows: 5, maxlength: 900, value: st.text, oninput: (e) => { st.text = e.target.value; upPrev(); sync(); } }), t("ads.c_text_hint")),
      drop(),
      h("div", { class: "grid2" }, field(t("ads.c_btn"), h("input", { class: "input", value: st.button_text, maxlength: 32, placeholder: t("ads.c_btn_ph"), oninput: (e) => { st.button_text = e.target.value; upPrev(); sync(); } })),
        field(t("ads.c_link"), h("input", { class: "input", value: st.button_url, placeholder: "https://t.me/…", autocapitalize: "none", oninput: (e) => { st.button_url = e.target.value; sync(); } }))),
      !cfg.external_links && h("div", { class: "tiny muted" }, t("ads.tme_only")),
      preview,
      h("div", { class: "h3" }, t("ads.model")), models.length > 1 ? model() : h("div", { class: "callout" }, icon("info", 16), h("div", null, t("ads.cpc_sub"))),
      field(t(st.pricing === "cpm" ? "ads.bid_cpm" : "ads.bid_cpc"), h("div", { class: "input-wrap" }, nin("bid"), h("span", { class: "suffix" }, "GRAM")), t("ads.bid_hint", { a: fmt(b.min), b: fmt(b.max) })),
      h("div", { class: "h3" }, t("ads.c_budget_title")),
      field(t("ads.c_budget"), h("div", { class: "input-wrap" }, nin("budget"), h("span", { class: "suffix" }, "GRAM")), t("ads.c_budget_hint", { b: fmt(S.user.balance) })),
      h("div", { class: "grid2" }, field(t("ads.c_days"), nin("days")), field(t("ads.c_ppd"), nin("posts_per_day"))),
      field(t("ads.c_minmembers"), nin("min_members"), t("ads.c_minmembers_hint")),
      estBox,
      h("div", { class: "callout" }, icon("clock", 18), h("div", null, t("ads.autostop"))),
      h("div", { class: "callout" }, icon("shield", 18), h("div", null, t("ads.review_note"))));
    upPrev(); drawEst(); sync();
  }
  async function submit() {
    if (!valid() || st.busy) return; st.busy = true; sync();
    try {
      const r = await api.adCreateCampaign({ title: st.title.trim(), text: st.text.trim(), button_text: st.button_text.trim(), button_url: st.button_url.trim(), pricing: st.pricing, bid: st.bid, budget: st.budget, posts_per_day: st.posts_per_day, days: st.days, min_members: st.min_members, blob: st.blob });
      patchUser({ balance: r.balance }); confetti(50); haptic.ok(); mainButton.hide();
      wrap.replaceChildren(h("div", { class: "card center", style: { padding: "30px 18px" } }, h("div", { style: { fontSize: "56px" } }, "⏳"), h("div", { class: "h2" }, t("ads.submitted")), h("div", { class: "muted", style: { margin: "6px 0 14px" } }, t("ads.submitted_sub")), h("button", { class: "btn", onclick: () => replace("adcampaign", { id: r.campaign.id }) }, t("common.continue"))));
    } catch (e) { fail(e); st.busy = false; sync(); }
  }
  draw(); refreshEst();
  return () => { clearTimeout(timer); mainButton.hide(); };
}

// ============================ campaign detail ============================
export async function campaign({ root, params, replace }) {
  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("ads.campaign"))), ...skeleton(3, 90));
  let d;
  try { d = await api.adCampaign(params.id); } catch (e) { root.replaceChildren(h("div", { class: "empty" }, t("err.generic"))); return; }
  const c = d.campaign, s = c.stats, live = ["active", "paused", "pending_review"].includes(c.status), kids = [];
  const act = async (a, body) => { try { const r = await api.adCampaignAction(c.id, a, body); if (r.balance != null) patchUser({ balance: r.balance }); haptic.ok(); replace("adcampaign", { id: c.id }); } catch (e) { fail(e); } };
  kids.push(h("div", { class: "pagehead" }, h("div", { class: "grow" }, h("div", { class: "h1", style: { fontSize: "21px" } }, c.title), h("div", { class: "small muted" }, c.end_at ? t("ads.ends", { d: new Date(c.end_at).toLocaleDateString() }) : "")), tag(c.status)));
  if (c.status === "rejected" && c.reject_reason) kids.push(h("div", { class: "callout warn" }, icon("info", 16), h("div", null, c.reject_reason)));
  if (c.status === "pending_review") kids.push(h("div", { class: "callout" }, icon("clock", 16), h("div", null, t("ads.review_note"))));
  kids.push(h("div", { class: "card" }, h("div", { class: "row between" }, h("div", null, h("div", { class: "small muted" }, t("ads.remaining")), h("div", { class: "h1 gold-text num", style: { fontSize: "28px" } }, money(c.remaining))), h("div", { style: { textAlign: "end" } }, h("div", { class: "small muted" }, t("ads.spent")), h("div", { class: "h3 num" }, money(c.spent)))),
    h("div", { class: "bar", style: { margin: "12px 0 4px" } }, h("i", { style: { width: Math.min(100, c.budget ? (c.spent / c.budget) * 100 : 0) + "%" } })), h("div", { class: "tiny muted" }, t("ads.budget_of", { b: fmt(c.budget) }))));
  kids.push(h("div", { class: "card flat mt" }, h("div", { class: "list" }, row(t("ads.model"), t("ads." + c.pricing + "_name")), row(t(c.pricing === "cpm" ? "ads.bid_cpm" : "ads.bid_cpc"), money(c.bid)))));
  kids.push(h("div", { class: "grid2 mt" }, stat(fmt(s.views), t("ads.impressions"), "eye", "g5"), stat(fmt(s.clicks), t("ads.clicks"), "trend", "g2"), stat(pct(s.ctr), t("ads.ctr"), "bolt", "g4"), stat(fmt(s.bot_starts), t("ads.bot_starts"), "bot", "g1"),
    stat(dash(s.cpc, fmt), t("ads.cpc"), "link", "g3"), stat(dash(s.cpm, fmt), t("ads.cpm"), "star", "g5")));
  kids.push(h("div", { class: "section" }, seriesBlock(d.series)));
  if (live || c.status === "finished") {
    const btns = [];
    if (c.status === "active") btns.push(h("button", { class: "btn sec", onclick: () => act("pause") }, icon("clock", 18), t("ads.pause")));
    if (c.status === "paused") btns.push(h("button", { class: "btn sec", onclick: () => act("resume") }, icon("bolt", 18), t("ads.resume")));
    if (live) btns.push(h("button", { class: "btn sec", onclick: () => fundSheet(c, (amount) => act("fund", { amount })) }, icon("plus", 18), t("ads.fund")));
    kids.push(h("div", { class: "grid2" }, ...btns));
    if (live) kids.push(h("button", { class: "btn sec", style: { marginTop: "10px", color: "var(--bad,#ff5470)" }, onclick: async () => { if (await confirmDialog(t("ads.stop_q", { n: fmt(c.remaining) }))) act("stop"); } }, icon("x", 18), t("ads.stop")));
  }
  kids.push(h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("ads.placements"))),
    d.placements.length ? h("div", { class: "card flat", style: { padding: "2px 14px" } }, h("div", { class: "list" }, ...d.placements.map((p) => h("div", { class: "item" },
      h("div", { class: "grow" }, h("div", { class: "h3", style: { fontSize: "14px" } }, p.channel_title || "—"), h("div", { class: "tiny muted" }, (p.posted_at ? ago(p.posted_at, t) : "—") + " · " + fmt(p.views) + " " + t("ads.views") + " · " + fmt(p.clicks) + " " + t("ads.clicks"))),
      h("div", { class: "col", style: { alignItems: "flex-end", gap: "4px" } }, h("div", { class: "amt num" }, "-" + short(p.cost)), tag(p.status)))))) : h("div", { class: "empty small" }, t("ads.no_posts"))));
  root.replaceChildren(...kids);
}

function fundSheet(c, onGo) {
  sheet((s) => {
    let amount = 100000;
    const inp = h("input", { class: "input num", inputmode: "numeric", value: amount, oninput: (e) => { amount = parseInt(e.target.value.replace(/\D/g, "")) || 0; } });
    s.render(h("div", { class: "h2", style: { marginBottom: "12px" } }, t("ads.fund")), h("div", { class: "col" }, h("div", { class: "field" }, h("label", null, t("ads.amount")), h("div", { class: "input-wrap" }, inp, h("span", { class: "suffix" }, "GRAM")), h("div", { class: "hint" }, t("ads.balance_is", { b: fmt(S.user.balance) }))),
      h("button", { class: "btn shine", onclick: () => { if (amount < 1) return; s.close(); onGo(amount); } }, icon("plus", 19), t("ads.fund"))));
  });
}
