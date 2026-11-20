import { h, icon, fmt, short, skeleton, sheet, toast, confetti, compressImage, ago, countUp, ring, dayLabel } from "../ui.js";
import { t } from "../i18n.js";
import { api } from "../api.js";
import { S, fail, patchUser, emit, TX } from "../state.js";
import { haptic, copyText, mainButton, shareLink } from "../tg.js";
import { txRow } from "./home.js";

const METHOD = { bkash: { name: "bKash", color: "#e2136e" }, nagad: { name: "Nagad", color: "#f6921e" }, rocket: { name: "Rocket", color: "#8c3494" } };
const WD = { usdt_trc20: "💎 USDT (TRC20)", usdt_erc20: "💎 USDT (ERC20)", bank: "🏦 Bank", mobile: "📱 Mobile wallet" };

export async function render({ root, params, push }) {
  let seg = params.seg || "deposit";
  const u = S.user;
  const balEl = h("span", { class: "num" }, "0");
  const wdable = Math.max(0, u.balance - (u.locked || 0));

  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("tab.wallet")), h("button", { class: "iconbtn", "aria-label": t("wallet.history"), onclick: () => { haptic.tap(); push("history"); } }, icon("clock", 20))));
  root.append(h("div", { class: "card", style: { background: "linear-gradient(140deg, rgba(124,92,255,.35), rgba(34,211,238,.18))" } },
    h("div", { class: "small muted", style: { fontWeight: 700 } }, t("home.balance")),
    h("div", { class: "row", style: { alignItems: "baseline", gap: "6px" } }, h("div", { class: "h1 gold-text", style: { fontSize: "34px" } }, balEl), h("span", { class: "muted", style: { fontWeight: 700 } }, "GRAM")),
    h("div", { class: "grid2", style: { marginTop: "12px" } },
      h("div", null, h("div", { class: "tiny muted" }, t("wallet.available")), h("div", { class: "h3 num" }, fmt(wdable))),
      h("div", null, h("div", { class: "tiny muted" }, icon("lock", 11), " ", t("wallet.locked")), h("div", { class: "h3 num" }, fmt(u.locked || 0))))));
  countUp(balEl, u.balance);

  const tabs = h("div", { class: "seg", style: { margin: "16px 0" } });
  const body = h("div");
  root.append(tabs, body);
  let clean = null;
  const drawTabs = () => tabs.replaceChildren(...[["deposit", t("wallet.deposit")], ["withdraw", t("wallet.withdraw")], ["gifts", t("wallet.gifts")]].map(([k, l]) => h("button", { class: k === seg ? "on" : "", onclick: () => { if (seg === k) return; haptic.select(); seg = k; drawTabs(); show(); } }, l)));
  function show() { clean && clean(); clean = null; mainButton.hide(); body.replaceChildren(); clean = ({ deposit, withdraw, gifts }[seg])(body); }
  drawTabs(); show();

  // pending requests
  const reqs = h("div");
  root.append(reqs);
  api.requests().then((r) => {
    if (!r.items.length) return;
    reqs.append(h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("wallet.requests"))), h("div", { class: "card flat" }, h("div", { class: "list" }, ...r.items.map((x) =>
      h("div", { class: "item" }, h("div", { class: "ico " + (x.kind === "deposit" ? "g2" : "g3") }, icon(x.kind === "deposit" ? "down" : "up", 19)), h("div", { class: "grow" }, h("div", { class: "h3", style: { fontSize: "14px" } }, t("wallet." + x.kind), " · ", (METHOD[x.method] && METHOD[x.method].name) || WD[x.method] || x.method), h("div", { class: "tiny muted" }, ago(x.created_at, t))),
        h("div", { class: "col", style: { alignItems: "flex-end", gap: "4px" } }, h("div", { class: "amt num" }, fmt(x.amount)), h("span", { class: "tag " + (x.status === "approved" ? "ok" : x.status === "rejected" ? "bad" : "warn") }, t("wallet.st_" + x.status)))))))));
    reqs.firstChild.style.marginBottom = "10px";
  }).catch(() => {});
  return () => { clean && clean(); mainButton.hide(); };
}

// ---------------- Deposit ----------------
function deposit(root) {
  const cfg = S.config, methods = Object.keys(METHOD).filter((k) => cfg.pay[k]);
  if (!methods.length) { root.append(h("div", { class: "empty" }, h("div", { class: "big" }, "🛠"), t("wallet.dep_off"))); return; }
  const st = { method: methods[0], amount: 500, trx: "", blob: null, preview: null, busy: false };
  const wrap = h("div", { class: "col gap-lg" });
  root.append(wrap);

  const valid = () => st.amount >= cfg.deposit.min && st.amount <= cfg.deposit.max && /^[A-Za-z0-9]{6,16}$/.test(st.trx.trim()) && st.blob;
  const syncMain = () => mainButton.set({ text: t("wallet.submit_dep"), disabled: !valid() || st.busy, loading: st.busy, onClick: submit });

  const dyn = h("div", { class: "col" });
  const amtPrev = h("div", { class: "hint ok num" });
  const setPrev = () => { amtPrev.textContent = st.amount >= cfg.deposit.min ? "= " + fmt(st.amount * cfg.deposit.rate) + " GRAM" : t("wallet.min_dep", { n: cfg.deposit.min }); amtPrev.className = "hint num " + (st.amount >= cfg.deposit.min && st.amount <= cfg.deposit.max ? "ok" : "err"); };

  function draw() {
    const num = cfg.pay[st.method];
    const mt = h("div", { class: "methods" }, methods.map((k) => h("button", { class: "method" + (st.method === k ? " on" : ""), onclick: () => { st.method = k; haptic.select(); draw(); } }, h("div", { class: "logo", style: { background: METHOD[k].color } }, METHOD[k].name[0]), METHOD[k].name)));
    const amount = h("input", { class: "input num", inputmode: "numeric", value: st.amount || "", placeholder: "500", oninput: (e) => { st.amount = parseInt(e.target.value.replace(/\D/g, "")) || 0; setPrev(); upd(); syncMain(); } });
    const trx = h("input", { class: "input", placeholder: "8N7A6D5EE7M", value: st.trx, autocapitalize: "characters", autocomplete: "off", oninput: (e) => { st.trx = e.target.value.trim(); upd(); syncMain(); } });
    const upd = () => { const c = st.amount >= cfg.deposit.min && st.amount <= cfg.deposit.max; amount.classList.toggle("err", !c && st.amount > 0); sendAmt.textContent = fmt(st.amount); };
    const sendAmt = h("b", { class: "num" }, fmt(st.amount));
    const drop = h("label", { class: "drop" + (st.preview ? " has" : "") }, st.preview ? h("img", { src: st.preview, alt: "" }) : [icon("image", 26), h("div", { class: "h3", style: { marginTop: "6px" } }, t("wallet.shot")), h("div", { class: "small" }, t("wallet.shot_sub"))],
      h("input", { type: "file", accept: "image/*", class: "hide", onchange: async (e) => { const f = e.target.files[0]; if (!f) return; try { st.blob = await compressImage(f); st.preview = URL.createObjectURL(st.blob); haptic.ok(); draw(); } catch (_) { toast(t("err.bad_image"), "err"); } } }));
    dyn.replaceChildren(
      h("div", { class: "card flat" },
        h("div", { class: "small muted" }, t("wallet.send_to")),
        h("div", { class: "row", style: { marginTop: "6px" } }, h("div", { class: "h2 num grow" }, num), h("button", { class: "btn sm sec", onclick: async () => { await copyText(num.replace(/\D/g, "")); haptic.ok(); toast(t("common.copied")); } }, icon("copy", 15), t("common.copy"))),
        h("div", { class: "small muted", style: { marginTop: "8px" } }, t("wallet.send_hint", { m: METHOD[st.method].name }), " ", sendAmt, " BDT")),
      h("div", { class: "field" }, h("label", null, h("span", null, t("wallet.trx")), h("span", { class: "faint" }, t("wallet.trx_once"))), trx), drop);
    wrap.replaceChildren(h("div", { class: "h3" }, t("wallet.method")), mt,
      h("div", { class: "field" }, h("label", null, t("wallet.amount_bdt")), h("div", { class: "input-wrap" }, amount, h("span", { class: "suffix" }, "BDT")), amtPrev,
        h("div", { class: "row", style: { flexWrap: "wrap", marginTop: "4px" } }, [100, 250, 500, 1000, 2000].map((n) => h("button", { class: "chip", onclick: () => { st.amount = n; haptic.tap(); draw(); } }, "৳" + fmt(n))))),
      dyn, h("div", { class: "callout" }, icon("shield", 18), h("div", null, t("wallet.dep_note"))));
    setPrev(); upd(); syncMain();
  }
  async function submit() {
    if (!valid() || st.busy) return; st.busy = true; syncMain();
    try {
      const r = await api.deposit({ method: st.method, amount_bdt: st.amount, trx_id: st.trx, blob: st.blob });
      confetti(40); haptic.ok(); mainButton.hide();
      wrap.replaceChildren(h("div", { class: "card center", style: { padding: "30px 18px" } }, h("div", { style: { fontSize: "56px" } }, "⏳"), h("div", { class: "h2" }, t("wallet.dep_sent")), h("div", { class: "muted", style: { margin: "6px 0" } }, t("wallet.dep_sent_sub", { n: fmt(r.gram) }))));
    } catch (e) { fail(e); st.busy = false; syncMain(); }
  }
  draw();
}

// ---------------- Withdraw ----------------
function withdraw(root) {
  const R = S.config.rules, u = S.user, cfg = S.config;
  const avail = Math.max(0, u.balance - (u.locked || 0));
  if (u.level < R.withdraw_min_level) {
    const need = R.withdraw_min_level - u.level, xpNeed = R.withdraw_min_level * R.xp_per_level - R.xp_per_level - u.xp;
    root.append(h("div", { class: "card center", style: { padding: "26px 18px" } },
      h("div", { style: { fontSize: "46px" } }, "🔒"), h("div", { class: "h2", style: { margin: "4px 0" } }, t("wallet.wd_locked", { n: R.withdraw_min_level })),
      h("div", { class: "muted small" }, t("wallet.wd_locked_sub", { n: R.withdraw_min_level })),
      h("div", { class: "bar", style: { margin: "18px 0 6px", height: "10px" } }, h("i", { style: { width: Math.min(100, (u.level / R.withdraw_min_level) * 100) + "%" } })),
      h("div", { class: "row between small" }, h("span", { class: "num" }, t("home.level", { n: u.level })), h("span", { class: "muted num" }, t("wallet.wd_levels_left", { n: need, xp: fmt(xpNeed) })), h("span", { class: "num" }, t("home.level", { n: R.withdraw_min_level })))),
      h("div", { class: "callout", style: { marginTop: "12px" } }, icon("info", 18), h("div", null, t("wallet.wd_tip"))));
    return;
  }
  const st = { amount: Math.min(avail, R.withdraw_max), method: cfg.withdraw.methods[0], details: "", busy: false };
  const wrap = h("div", { class: "col gap-lg" }); root.append(wrap);
  const ok = () => st.amount >= R.withdraw_min && st.amount <= Math.min(R.withdraw_max, avail) && st.details.trim().length >= 5;
  const sync = () => mainButton.set({ text: t("wallet.request_wd") + (st.amount ? " · " + short(st.amount) : ""), disabled: !ok() || st.busy, loading: st.busy, onClick: submit });
  function draw() {
    const amount = h("input", { class: "input num", inputmode: "numeric", value: st.amount || "", oninput: (e) => { st.amount = parseInt(e.target.value.replace(/\D/g, "")) || 0; hint(); sync(); } });
    const hintEl = h("div", { class: "hint" });
    const hint = () => { const bad = st.amount && (st.amount < R.withdraw_min || st.amount > Math.min(R.withdraw_max, avail)); hintEl.className = "hint " + (bad ? "err" : ""); hintEl.textContent = t("wallet.wd_range", { a: fmt(R.withdraw_min), b: fmt(Math.min(R.withdraw_max, avail)) }); amount.classList.toggle("err", !!bad); };
    wrap.replaceChildren(
      h("div", { class: "field" }, h("label", null, h("span", null, t("wallet.amount")), h("a", { class: "link", onclick: () => { st.amount = Math.min(avail, R.withdraw_max); draw(); } }, "MAX")), h("div", { class: "input-wrap" }, amount, h("span", { class: "suffix" }, "GRAM")), hintEl),
      h("div", { class: "field" }, h("label", null, t("wallet.wd_method")), h("div", { class: "col", style: { gap: "8px" } }, cfg.withdraw.methods.map((k) => h("button", { class: "lang" + (st.method === k ? " on" : ""), onclick: () => { st.method = k; haptic.select(); draw(); } }, WD[k], st.method === k && icon("check", 18))))),
      h("div", { class: "field" }, h("label", null, t("wallet.wd_details")), h("input", { class: "input", placeholder: t("wallet.wd_details_ph"), value: st.details, autocomplete: "off", oninput: (e) => { st.details = e.target.value; sync(); } })),
      h("div", { class: "callout warn" }, icon("clock", 18), h("div", null, t("wallet.wd_note", { n: R.retention_days }))));
    hint(); sync();
  }
  async function submit() {
    if (!ok() || st.busy) return; st.busy = true; sync();
    try { const r = await api.withdraw({ amount: st.amount, method: st.method, details: st.details.trim() }); patchUser({ balance: r.balance }); haptic.ok(); confetti(40); mainButton.hide(); wrap.replaceChildren(h("div", { class: "card center", style: { padding: "30px 18px" } }, h("div", { style: { fontSize: "56px" } }, "✅"), h("div", { class: "h2" }, t("wallet.wd_sent")), h("div", { class: "muted", style: { marginTop: "6px" } }, t("wallet.wd_sent_sub")))); }
    catch (e) { fail(e); st.busy = false; sync(); }
  }
  draw();
}

// ---------------- Gifts: promo codes + checks ----------------
function gifts(root) {
  const promo = h("input", { class: "input", placeholder: "PROMO CODE", autocapitalize: "characters", style: { textTransform: "uppercase" } });
  const chk = h("input", { class: "input", placeholder: "CHECK CODE", autocapitalize: "characters", style: { textTransform: "uppercase" } });
  const chkPw = h("input", { class: "input", type: "password", placeholder: t("wallet.check_pw_ph"), autocomplete: "off" });
  const busy = (btn, on) => { btn.disabled = on; };
  const redeemPromo = h("button", { class: "btn", onclick: async () => { if (!promo.value.trim()) return; busy(redeemPromo, true); try { const r = await api.redeemVoucher(promo.value.trim()); patchUser({ balance: r.balance }); confetti(); haptic.ok(); toast("+" + fmt(r.amount) + " GRAM"); promo.value = ""; } catch (e) { fail(e); } busy(redeemPromo, false); } }, icon("ticket", 19), t("wallet.redeem"));
  const redeemChk = h("button", { class: "btn sec", onclick: async () => { if (!chk.value.trim()) return; busy(redeemChk, true); try { const r = await api.redeemCheck(chk.value.trim(), chkPw.value.trim() || null); patchUser({ balance: r.balance }); confetti(); haptic.ok(); toast("+" + fmt(r.amount) + " GRAM"); chk.value = ""; } catch (e) { fail(e); } busy(redeemChk, false); } }, icon("gift", 19), t("wallet.redeem_check"));
  root.append(h("div", { class: "col gap-lg" },
    h("div", { class: "card flat col" }, h("div", { class: "row" }, h("div", { class: "ico g4" }, icon("ticket", 20)), h("div", null, h("div", { class: "h3" }, t("wallet.promo"), ), h("div", { class: "small muted" }, t("wallet.promo_sub")))), promo, redeemPromo),
    h("div", { class: "card flat col" }, h("div", { class: "row" }, h("div", { class: "ico g1" }, icon("gift", 20)), h("div", null, h("div", { class: "h3" }, t("wallet.checks")), h("div", { class: "small muted" }, t("wallet.checks_sub")))), chk, chkPw, redeemChk,
      h("button", { class: "btn sec", onclick: createCheckSheet }, icon("plus", 19), t("wallet.create_check")))));
}

function createCheckSheet() {
  sheet((s) => {
    const st = { amount: 5000, uses: 5, password: "", busy: false };
    const total = h("b", { class: "num gold-text" });
    const upd = () => { total.textContent = fmt(st.amount * st.uses) + " GRAM"; };
    const btn = h("button", { class: "btn shine", onclick: async () => {
      if (st.busy) return; if (st.amount < 1 || st.uses < 1) return toast(t("err.invalid"), "err");
      st.busy = true; btn.disabled = true;
      try {
        const r = await api.createCheck({ amount: st.amount, uses: st.uses, password: st.password || null }); patchUser({ balance: r.balance }); confetti(40); haptic.ok();
        s.render(h("div", { class: "center" }, h("div", { style: { fontSize: "52px" } }, "🎁"), h("div", { class: "h2" }, t("wallet.check_ready")), h("div", { class: "code", style: { margin: "14px 0" } }, r.link),
          h("div", { class: "grid2" }, h("button", { class: "btn sec", onclick: async () => { await copyText(r.link); toast(t("common.copied")); } }, icon("copy", 18), t("common.copy")), h("button", { class: "btn", onclick: () => shareLink(r.link, t("wallet.check_share")) }, icon("share", 18), t("common.share")))));
      } catch (e) { fail(e); st.busy = false; btn.disabled = false; }
    } }, icon("gift", 19), t("wallet.create_check"));
    s.render(h("div", { class: "h2", style: { marginBottom: "14px" } }, t("wallet.create_check")), h("div", { class: "col" },
      h("div", { class: "field" }, h("label", null, t("wallet.per_use")), h("input", { class: "input num", inputmode: "numeric", value: st.amount, oninput: (e) => { st.amount = parseInt(e.target.value.replace(/\D/g, "")) || 0; upd(); } })),
      h("div", { class: "field" }, h("label", null, t("wallet.uses")), h("input", { class: "input num", inputmode: "numeric", value: st.uses, oninput: (e) => { st.uses = parseInt(e.target.value.replace(/\D/g, "")) || 0; upd(); } })),
      h("div", { class: "field" }, h("label", null, t("wallet.check_pw")), h("input", { class: "input", placeholder: t("common.optional"), oninput: (e) => { st.password = e.target.value; } })),
      h("div", { class: "row between" }, h("span", { class: "muted" }, t("promo.sum_total")), total), btn));
    upd();
  });
}

// ---------------- History (sub page) ----------------
export async function history({ root }) {
  let kind = "all";
  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("wallet.history"))));
  const chips = h("div", { class: "chips" }), list = h("div");
  root.append(chips, list);
  const draw = () => chips.replaceChildren(...[["all", t("earn.all")], ["in", t("wallet.income")], ["out", t("wallet.expense")]].map(([k, l]) => h("button", { class: "chip" + (k === kind ? " on" : ""), onclick: () => { kind = k; haptic.select(); draw(); load(); } }, l)));
  async function load() {
    list.replaceChildren(...skeleton(5, 56));
    try {
      const r = await api.history(null, kind);
      if (!r.items.length) return list.replaceChildren(h("div", { class: "empty" }, h("div", { class: "big" }, "🧾"), t("wallet.no_history")));
      const groups = new Map();
      r.items.forEach((x) => { const k = dayLabel(x.created_at, t); (groups.get(k) || groups.set(k, []).get(k)).push(x); });
      list.replaceChildren(...[...groups].map(([day, items]) => h("div", null, h("div", { class: "day" }, day), h("div", { class: "card flat", style: { padding: "2px 14px" } }, h("div", { class: "list" }, ...items.map(txRow))))));
    } catch (e) { list.replaceChildren(h("div", { class: "empty" }, t("err.generic"))); }
  }
  draw(); load();
}
