import { h, icon, fmt, short, skeleton, sheet, toast, confetti, ago, avatarFor, sleep } from "../ui.js";
import { t } from "../i18n.js";
import { api } from "../api.js";
import { S, TYPES, TYPE_ORDER, fail, patchUser, emit } from "../state.js";
import { haptic, mainButton, confirmDialog, openTelegramLink } from "../tg.js";

export async function render({ root, params, go }) {
  let tab = params.tab || "create";
  let cleanup = null, alive = true;

  const body = h("div");
  const tabs = h("div", { class: "seg", style: { marginBottom: "16px" } });
  const drawTabs = () => {
    const rv = S.badges.reviews;
    tabs.replaceChildren(...[["create", t("promo.create")], ["mine", t("promo.mine")], ["reviews", t("promo.reviews") + (rv ? ` (${rv})` : "")]].map(([k, label]) =>
      h("button", { class: k === tab ? "on" : "", onclick: () => { if (tab === k) return; haptic.select(); tab = k; drawTabs(); show(); } }, label)));
  };
  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("tab.promote"))), tabs, body);

  function show() {
    cleanup && cleanup(); cleanup = null; mainButton.hide();
    body.replaceChildren();
    const fn = tab === "create" ? wizard : tab === "mine" ? myCampaigns : reviews;
    const out = fn(body, { go, switchTab: (k) => { tab = k; drawTabs(); show(); } });
    Promise.resolve(out).then((c) => { if (!alive) c && c(); else cleanup = c; });
  }
  drawTabs(); show();
  return () => { alive = false; cleanup && cleanup(); };
}

// ======================== Create wizard ========================
function wizard(root, { switchTab, go }) {
  const R = S.config.rules, P = S.config.pricing;
  const st = { step: 1, type: null, link: "", target: null, resolving: false, linkErr: "", premium: false, reward: 0, slots: 100, autoRenew: false, busy: false };
  const price = (type = st.type) => { const p = P[type], k = st.premium && type !== "boost_7day" ? R.premium_multiplier : 1; return { min: p.min * k, suggested: p.suggested * k, max: p.max * k }; };
  const cost = () => { const budget = st.reward * st.slots, fee = Math.ceil(budget * R.commission); return { budget, fee, total: budget + fee }; };

  const wrap = h("div", { class: "col gap-lg" });
  root.append(wrap);
  let linkTimer = 0;

  function bar() { return h("div", { class: "stepper" }, [1, 2, 3, 4].map((i) => h("i", { class: i <= st.step ? "on" : "" }))); }
  function nav() {
    return h("div", { class: "row", style: { marginBottom: "6px" } },
      st.step > 1 ? h("button", { class: "btn sec sm", onclick: () => { haptic.tap(); st.step--; draw(); } }, icon("chevL", 16), t("common.back")) : h("span"),
      h("span", { class: "grow" }), h("span", { class: "small muted" }, t("promo.step", { n: st.step, m: 4 })));
  }

  function draw() {
    wrap.replaceChildren(bar(), nav(), [step1, step2, step3, step4][st.step - 1]());
    scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---- step 1: type ----
  function step1() {
    const el = h("div", { class: "col" }, h("div", null, h("div", { class: "h2" }, t("promo.s1_title")), h("div", { class: "small muted" }, t("promo.s1_sub"))));
    const g = h("div", { class: "typegrid" });
    TYPE_ORDER.forEach((k) => {
      const m = TYPES[k];
      g.append(h("button", { class: "typecard" + (st.type === k ? " on" : ""), onclick: () => { haptic.select(); st.type = k; st.target = null; st.reward = price(k).suggested; st.link = ""; draw(); } },
        h("div", { class: "ico " + m.cls }, icon(m.icon, 21)),
        h("div", { class: "nm" }, t("type." + k)),
        h("div", { class: "pr" }, t("promo.from", { n: fmt(P[k].min) })), h("div", { class: "tiny faint" }, t("promo.hint_" + k))));
    });
    el.append(g);
    setMain(t("common.continue"), !st.type, () => { st.step = 2; draw(); });
    return el;
  }

  // ---- step 2: target ----
  function step2() {
    const isBot = st.type === "bot_start", isPost = st.type === "post_view";
    const input = h("input", { class: "input" + (st.linkErr ? " err" : ""), placeholder: isBot ? "@your_bot" : isPost ? "https://t.me/channel/123" : "@channel or t.me/link", value: st.link, autocapitalize: "off", autocomplete: "off", spellcheck: "false", inputmode: "url", oninput: (e) => {
      st.link = e.target.value; st.target = null; st.linkErr = ""; clearTimeout(linkTimer);
      if (st.link.trim().length < 4) { renderPreview(); setMainStep2(); return; }
      linkTimer = setTimeout(resolve, 600); st.resolving = true; renderPreview(); setMainStep2();
    } });
    const preview = h("div");
    const renderPreview = () => {
      if (st.resolving) return preview.replaceChildren(h("div", { class: "sk", style: { height: "76px" } }));
      if (st.linkErr) return preview.replaceChildren(h("div", { class: "hint err", style: { marginTop: "2px" } }, st.linkErr));
      if (!st.target) return preview.replaceChildren();
      const tg = st.target;
      preview.replaceChildren(h("div", { class: "card flat" },
        h("div", { class: "row" }, avatarFor(tg.title), h("div", { class: "grow" }, h("div", { class: "h3 ellipsis" }, tg.title), h("div", { class: "small muted" }, "@" + tg.username, tg.members ? " · " + fmt(tg.members) + " " + t("promo.members") : "")), h("span", { class: "tag ok" }, icon("check", 12), t("promo.found"))),
        !tg.bot_admin && h("div", { class: "callout warn", style: { marginTop: "12px" } }, icon("warn", 18), h("div", null, t("promo.need_admin", { bot: "@" + S.config.bot_username })))));
    };
    async function resolve() {
      try { const r = await api.resolveTarget(st.type, st.link); st.target = r; st.linkErr = ""; haptic.ok(); }
      catch (e) { st.target = null; st.linkErr = (e.code && t("err." + e.code) !== "err." + e.code) ? t("err." + e.code) : e.message; haptic.warn(); }
      st.resolving = false; renderPreview(); setMainStep2();
    }
    const setMainStep2 = () => setMain(t("common.continue"), !st.target || st.resolving, () => { st.step = 3; draw(); });
    const el = h("div", { class: "col" },
      h("div", null, h("div", { class: "h2" }, t("promo.s2_title")), h("div", { class: "small muted" }, t(isBot ? "promo.s2_bot" : isPost ? "promo.s2_post" : "promo.s2_chat"))),
      h("div", { class: "field" }, h("label", null, t("promo.link")), h("div", { class: "input-wrap" }, input)),
      preview,
      !isBot && !isPost && h("div", { class: "callout" }, icon("shield", 18), h("div", null, t("promo.admin_tip", { bot: "@" + S.config.bot_username }))));
    renderPreview(); setMainStep2();
    if (st.link && !st.target && !st.resolving) { st.resolving = true; renderPreview(); resolve(); }
    return el;
  }

  // ---- step 3: price & audience ----
  function step3() {
    const p = price();
    if (st.reward < p.min || st.reward > p.max) st.reward = Math.min(p.max, Math.max(p.min, st.reward || p.suggested));
    const val = h("div", { class: "h1 num gold-text" }, fmt(st.reward));
    const rng = h("input", { type: "range", min: p.min, max: p.max, step: p.max > 20000 ? 500 : p.max > 2000 ? 50 : 10, value: st.reward, oninput: (e) => { st.reward = +e.target.value; val.textContent = fmt(st.reward); e.target.style.setProperty("--p", pct(e.target) + "%"); haptic.select(); } });
    rng.style.setProperty("--p", pct(rng) + "%");
    function pct(r) { return ((r.value - r.min) / (r.max - r.min)) * 100; }
    const chips = h("div", { class: "row", style: { flexWrap: "wrap" } },
      [["min", p.min], ["suggested", p.suggested], ["max", p.max]].map(([k, v]) => h("button", { class: "chip", onclick: () => { st.reward = v; draw(); haptic.tap(); } }, t("promo.p_" + k), " ", fmt(v))));
    const canPrem = st.type !== "boost_7day";
    const el = h("div", { class: "col gap-lg" },
      h("div", null, h("div", { class: "h2" }, t("promo.s3_title")), h("div", { class: "small muted" }, t("promo.s3_sub"))),
      canPrem && h("div", { class: "seg" },
        h("button", { class: !st.premium ? "on" : "", onclick: () => { st.premium = false; draw(); haptic.select(); } }, "👥 " + t("promo.aud_all")),
        h("button", { class: st.premium ? "on" : "", onclick: () => { st.premium = true; draw(); haptic.select(); } }, "⭐ " + t("promo.aud_prem"))),
      st.premium && h("div", { class: "hint" }, t("promo.prem_note", { n: R.premium_multiplier })),
      h("div", { class: "card flat center" }, h("div", { class: "small muted" }, t("promo.reward_per")), val, h("div", { class: "small muted", style: { marginBottom: "12px" } }, "GRAM"), rng, h("div", { style: { marginTop: "12px" } }, chips)),
      st.type !== "boost_7day" && switchRow("refresh", t("promo.auto_renew"), t("promo.auto_renew_sub"), st.autoRenew, (v) => { st.autoRenew = v; }));
    setMain(t("common.continue"), false, () => { st.step = 4; draw(); });
    return el;
  }

  // ---- step 4: slots & confirm ----
  function step4() {
    const slotsVal = h("div", { class: "h1 num" }, fmt(st.slots));
    const sum = h("div", { class: "summary card flat" });
    const warn = h("div");
    const toSlots = (v) => { let n = Math.round(Math.pow(10, (v / 100) * Math.log10(R.slots_max))); if (n > 1000) n = Math.round(n / 100) * 100; else if (n > 100) n = Math.round(n / 10) * 10; return Math.min(R.slots_max, Math.max(R.slots_min, n)); };
    const fromSlots = (n) => (Math.log10(Math.max(1, n)) / Math.log10(R.slots_max)) * 100;
    const rng = h("input", { type: "range", min: 0, max: 100, step: 0.5, value: fromSlots(st.slots), oninput: (e) => { st.slots = toSlots(+e.target.value); slotsVal.textContent = fmt(st.slots); e.target.style.setProperty("--p", e.target.value + "%"); refresh(); haptic.select(); } });
    rng.style.setProperty("--p", rng.value + "%");
    const refresh = () => {
      const c = cost(), left = S.user.balance - c.total, enough = left >= 0;
      sum.replaceChildren(
        line(t("promo.sum_budget"), fmt(c.budget)), line(t("promo.sum_fee", { n: Math.round(R.commission * 100) }), fmt(c.fee)),
        h("div", { class: "line total" }, h("span", null, t("promo.sum_total")), h("span", { class: "num gold-text" }, fmt(c.total) + " GRAM")),
        h("div", { class: "line small muted" }, h("span", null, t("promo.sum_after")), h("span", { class: "num", style: { color: enough ? "" : "var(--bad)" } }, fmt(left))));
      warn.replaceChildren(enough ? "" : h("div", { class: "callout bad" }, icon("warn", 18), h("div", null, h("b", null, t("promo.no_funds", { n: fmt(-left) })), h("div", { class: "mt", style: { marginTop: "8px" } }, h("button", { class: "btn sm", onclick: () => go("wallet", { seg: "deposit" }) }, icon("plus", 15), t("home.deposit"))))));
      setMain(t("promo.launch") + " · " + short(c.total), !enough || st.busy, launch, st.busy);
    };
    const presets = h("div", { class: "row", style: { flexWrap: "wrap" } }, [10, 50, 100, 500, 1000, 5000].map((n) => h("button", { class: "chip", onclick: () => { st.slots = n; draw(); haptic.tap(); } }, fmt(n))));
    const target = st.target;
    const el = h("div", { class: "col gap-lg" },
      h("div", null, h("div", { class: "h2" }, t("promo.s4_title")), h("div", { class: "small muted" }, t("promo.s4_sub"))),
      h("div", { class: "card flat" }, h("div", { class: "row" }, avatarFor(target.title), h("div", { class: "grow" }, h("div", { class: "h3 ellipsis" }, target.title), h("div", { class: "small muted" }, t("type." + st.type), st.premium ? " · ⭐" : "")), h("div", { class: "amt num gold-text" }, fmt(st.reward) + " /" + t("promo.user")))),
      h("div", { class: "card flat center" }, h("div", { class: "small muted" }, t("promo.slots")), slotsVal, h("div", { class: "small muted", style: { marginBottom: "12px" } }, t("promo.people")), rng, h("div", { style: { marginTop: "12px" } }, presets)),
      sum, warn);
    refresh();
    async function launch() {
      if (st.busy) return; st.busy = true; refresh();
      try {
        const title = target.title;
        const r = await api.createCampaign({ type: st.type, link: st.link, title, username: target.username, premium: st.premium, reward: st.reward, slots: st.slots, auto_renew: st.autoRenew });
        patchUser({ balance: r.balance }); S.user.balance = r.balance; emit();
        confetti(); haptic.ok(); mainButton.hide();
        wrap.replaceChildren(h("div", { class: "center card", style: { padding: "34px 20px" } }, h("div", { style: { fontSize: "62px" } }, "🚀"), h("div", { class: "h1" }, t("promo.live")), h("div", { class: "muted", style: { margin: "8px 0 22px" } }, t("promo.live_sub", { n: fmt(st.slots) })),
          h("button", { class: "btn", onclick: () => switchTab("mine") }, t("promo.view_mine"))));
        scrollTo({ top: 0, behavior: "smooth" });
      } catch (e) { fail(e); st.busy = false; refresh(); }
    }
    return el;
  }

  draw();
  return () => { clearTimeout(linkTimer); mainButton.hide(); };
}

function setMain(text, disabled, onClick, loading = false) { mainButton.set({ text, disabled, loading, onClick: () => { haptic.press(); onClick(); } }); }
const line = (k, v) => h("div", { class: "line" }, h("span", { class: "muted" }, k), h("span", { class: "num" }, v));

export function switchRow(ic, title, sub, on, onChange) {
  const sw = h("button", { class: "switch" + (on ? " on" : ""), role: "switch", "aria-checked": String(on), onclick: () => { on = !on; sw.classList.toggle("on", on); sw.setAttribute("aria-checked", String(on)); haptic.select(); onChange(on); } });
  return h("div", { class: "card flat" }, h("div", { class: "row" }, h("div", { class: "ico g1" }, icon(ic, 19)), h("div", { class: "grow" }, h("div", { class: "h3" }, title), h("div", { class: "small muted" }, sub)), sw));
}

// ======================== My campaigns ========================
async function myCampaigns(root, { switchTab }) {
  const list = h("div", null, ...skeleton(3, 110));
  root.append(list);
  async function load() {
    try {
      const r = await api.campaigns();
      if (!r.items.length) return list.replaceChildren(h("div", { class: "empty" }, h("div", { class: "big" }, "📣"), h("div", { class: "h3" }, t("promo.none")), h("div", { class: "small" }, t("promo.none_sub")), h("div", { class: "mt" }, h("button", { class: "btn sm", onclick: () => switchTab("create") }, icon("plus", 15), t("promo.create")))));
      list.replaceChildren(...r.items.map((c, i) => campaignCard(c, i, load, switchTab)));
    } catch (e) { list.replaceChildren(h("div", { class: "empty" }, t("err.generic"))); }
  }
  await load();
}

const STATUS = { active: "ok", paused: "warn", completed: "info", cancelled: "bad" };
function campaignCard(c, i, reload, switchTab) {
  const m = TYPES[c.type], pct = Math.round((c.completed / c.slots_total) * 100);
  return h("div", { class: "card tap", style: { marginBottom: "10px", animation: `pageIn .4s ${i * 0.05}s both var(--ease)` }, onclick: () => openCampaign(c, reload, switchTab) },
    h("div", { class: "row" }, h("div", { class: "ico " + m.cls }, icon(m.icon, 20)), h("div", { class: "grow" }, h("div", { class: "h3 ellipsis" }, c.title), h("div", { class: "small muted" }, t("type." + c.type))), h("span", { class: "tag " + STATUS[c.status] }, t("promo.st_" + c.status))),
    h("div", { class: "bar gold" }, h("i", { style: { width: pct + "%" } })),
    h("div", { class: "row between small", style: { marginTop: "8px" } }, h("span", { class: "muted num" }, `${fmt(c.completed)} / ${fmt(c.slots_total)}`), c.pending_proofs > 0 ? h("span", { class: "tag warn" }, "📨 " + c.pending_proofs + " " + t("promo.to_review")) : h("span", { class: "muted num" }, fmt(c.spent) + " GRAM")));
}

function openCampaign(c, reload, switchTab) {
  sheet((s) => {
    const m = TYPES[c.type];
    const run = async (action, msg) => { try { const r = await api.campaignAction(c.id, action); if (r.balance != null) { S.user.balance = r.balance; emit(); } toast(msg || t("common.done")); s.close(); reload(); } catch (e) { fail(e); } };
    const left = (c.slots_total - c.completed) * c.reward;
    const rows = [[t("promo.reward_per"), fmt(c.reward) + " GRAM"], [t("promo.slots"), `${fmt(c.completed)} / ${fmt(c.slots_total)}`], [t("promo.spent"), fmt(c.spent) + " GRAM"], [t("promo.audience"), c.premium ? "⭐ " + t("promo.aud_prem") : "👥 " + t("promo.aud_all")], [t("promo.created"), ago(c.created_at, t)]];
    s.render(
      h("div", { class: "row", style: { marginBottom: "14px" } }, h("div", { class: "ico " + m.cls, style: { width: "52px", height: "52px" } }, icon(m.icon, 26)), h("div", { class: "grow" }, h("div", { class: "h2 ellipsis" }, c.title), h("div", { class: "small muted" }, "@" + c.username)), h("span", { class: "tag " + STATUS[c.status] }, t("promo.st_" + c.status))),
      h("div", { class: "bar gold", style: { marginBottom: "14px" } }, h("i", { style: { width: Math.round((c.completed / c.slots_total) * 100) + "%" } })),
      h("div", { class: "card flat summary" }, rows.map(([k, v]) => line(k, v))),
      c.pending_proofs > 0 && h("button", { class: "btn gold", style: { marginTop: "12px" }, onclick: () => { s.close(); switchTab("reviews"); } }, icon("inbox", 19), t("promo.review_n", { n: c.pending_proofs })),
      h("div", { class: "grid2", style: { marginTop: "12px" } },
        c.status === "active" && h("button", { class: "btn sec", onclick: () => run("pause") }, icon("pause", 18), t("promo.pause")),
        c.status === "paused" && h("button", { class: "btn ok", onclick: () => run("resume") }, icon("play", 18), t("promo.resume")),
        ["active", "paused"].includes(c.status) && h("button", { class: "btn bad", onclick: async () => { if (await confirmDialog(t("promo.cancel_q", { n: fmt(left) }))) run("cancel", t("promo.refunded", { n: fmt(left) })); } }, icon("trash", 18), t("promo.cancel"))));
  });
}

// ======================== Proof reviews ========================
async function reviews(root) {
  const list = h("div", null, ...skeleton(2, 220));
  root.append(h("div", { class: "callout", style: { marginBottom: "14px" } }, icon("info", 18), h("div", null, t("promo.review_info", { h: S.config.rules.proof_review_hours }))), list);
  async function load() {
    try {
      const r = await api.reviews();
      S.badges.reviews = r.items.length; emit();
      if (!r.items.length) return list.replaceChildren(h("div", { class: "empty" }, h("div", { class: "big" }, "✅"), h("div", { class: "h3" }, t("promo.no_reviews")), h("div", { class: "small" }, t("promo.no_reviews_sub"))));
      list.replaceChildren(...r.items.map((p) => proofCard(p, async (action) => {
        try { await api.reviewAction(p.id, action); haptic.ok(); toast(action === "approve" ? t("promo.approved") : t("promo.rejected")); load(); } catch (e) { fail(e); }
      })));
    } catch (e) { list.replaceChildren(h("div", { class: "empty" }, t("err.generic"))); }
  }
  await load();
}

function proofCard(p, decide) {
  const hrs = Math.max(0, Math.round((new Date(p.expires_at) - Date.now()) / 3600000));
  const img = h("img", { src: p.image, alt: "proof", style: { width: "100%", height: "200px", objectFit: "cover", borderRadius: "14px", cursor: "zoom-in" }, onclick: () => sheet((s) => s.render(h("img", { src: p.image, style: { width: "100%", borderRadius: "16px" } }))) });
  return h("div", { class: "card", style: { marginBottom: "12px" } },
    h("div", { class: "row between", style: { marginBottom: "10px" } }, h("div", null, h("div", { class: "h3" }, p.campaign_title), h("div", { class: "small muted" }, p.worker + " · " + ago(p.created_at, t))), h("span", { class: "tag warn" }, icon("clock", 11), t("promo.auto_in", { n: hrs }))),
    img,
    h("div", { class: "grid2", style: { marginTop: "12px" } },
      h("button", { class: "btn bad", onclick: () => decide("reject") }, icon("x", 18), t("promo.reject")),
      h("button", { class: "btn ok", onclick: () => decide("approve") }, icon("check", 18), t("promo.approve"))));
}
