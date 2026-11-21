import { h, icon, fmt, short, skeleton, sheet, toast, confetti, compressImage, ago, countUp, ring, dayLabel } from "../ui.js";
import { t } from "../i18n.js";
import { api } from "../api.js";
import { S, fail, patchUser, emit, TX } from "../state.js";
import { haptic, copyText, mainButton, shareLink, openInvoice } from "../tg.js";
import { txRow } from "./home.js";

const METHOD = { bkash: { name: "bKash", color: "#e2136e" }, nagad: { name: "Nagad", color: "#f6921e" }, rocket: { name: "Rocket", color: "#8c3494" } };
const WD = { usdt_trc20: "💎 USDT (TRC20)", usdt_erc20: "💎 USDT (ERC20)", bank: "🏦 Bank", mobile: "📱 Mobile wallet", ton: "💠 TON" };

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
  const cfg = S.config, local = Object.keys(METHOD).some((k) => cfg.pay[k]);
  const modes = [cfg.stars && ["stars", "⭐ " + t("wallet.stars"), starsDeposit], cfg.ton && cfg.ton.deposit && ["ton", "💠 " + t("wallet.ton"), tonDeposit], local && ["local", t("wallet.local"), localDeposit]].filter(Boolean);
  if (!modes.length) return localDeposit(root);
  let mode = modes[0][0], clean = null;
  const seg = h("div", { class: "seg", style: { marginBottom: "14px" } }), body = h("div");
  root.append(...(modes.length > 1 ? [seg] : []), body);
  const draw = () => {
    clean && clean(); clean = null; mainButton.hide(); body.replaceChildren();
    seg.replaceChildren(...modes.map(([k, l]) => h("button", { class: k === mode ? "on" : "", onclick: () => { if (mode === k) return; mode = k; haptic.select(); draw(); } }, l)));
    clean = modes.find((m) => m[0] === mode)[2](body);
  };
  draw();
  return () => { clean && clean(); };
}

function tonDeposit(root) {
  const wrap = h("div", { class: "col gap-lg" }); root.append(wrap);
  let info = null, busy = false;
  const field = (label, val, warn) => h("div", { class: "field" }, h("label", null, label),
    h("div", { class: "row", style: { gap: "8px" } }, h("div", { class: "input num grow", style: { wordBreak: "break-all", fontSize: "13px" } }, val), h("button", { class: "btn sm sec", onclick: async () => { await copyText(val); haptic.ok(); toast(t("common.copied")); } }, icon("copy", 18))), warn && h("div", { class: "hint err" }, warn));
  const draw = () => {
    if (!info) return;
    if (!info.enabled) return wrap.replaceChildren(h("div", { class: "callout warn" }, icon("info", 18), h("div", null, t("wallet.ton_off"))));
    const link = `https://app.tonkeeper.com/transfer/${info.wallet}?text=${encodeURIComponent(info.memo)}`;
    wrap.replaceChildren(
      h("div", { class: "card flat center" }, h("div", { style: { fontSize: "40px" } }, "💠"), h("div", { class: "h3" }, t("wallet.ton_title")), h("div", { class: "small muted", style: { marginTop: "4px" } }, t("wallet.ton_how"))),
      field(t("wallet.ton_address"), info.wallet), field(t("wallet.ton_memo"), info.memo, t("wallet.ton_memo_warn")),
      info.ton_usd ? h("div", { class: "small muted center" }, t("wallet.ton_rate", { p: info.ton_usd.toFixed(2), g: fmt(info.gram_per_ton) })) : null,
      h("div", { class: "tiny muted center" }, t("wallet.ton_min", { n: info.min_usd })),
      h("div", { class: "grid2" }, h("button", { class: "btn sec", onclick: () => import("../tg.js").then((m) => m.openLink(link)) }, icon("link", 18), t("wallet.ton_open")),
        h("button", { class: "btn", disabled: busy, onclick: check }, icon("check", 18), t("wallet.ton_check"))),
      info.history.length ? h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("wallet.ton_history"))),
        h("div", { class: "card flat", style: { padding: "2px 14px" } }, h("div", { class: "list" }, info.history.map((d) => h("div", { class: "item" }, h("div", { class: "ico g2" }, icon("down", 19)),
          h("div", { class: "grow" }, h("div", { class: "h3" }, d.ton.toFixed(3) + " TON ≈ $" + d.usd.toFixed(2)), h("div", { class: "tiny muted" }, ago(d.created_at))), h("div", { class: "num" }, d.coins ? "+" + fmt(d.coins) : "—")))))) : null);
  };
  async function check() {
    if (busy) return; busy = true; draw();
    try {
      const before = S.user.balance, r = await api.tonCheck();
      const b = await api.bootstrap(); patchUser({ balance: b.user.balance });
      if (r.credited || b.user.balance > before) { haptic.ok(); confetti(50); toast(t("wallet.ton_ok"), "ok"); info = await api.tonInfo(); } else toast(t("wallet.ton_none"));
    } catch (e) { fail(e); }
    busy = false; draw();
  }
  api.tonInfo().then((r) => { info = r; draw(); }).catch((e) => { fail(e); });
  wrap.append(...skeleton(2));
  return () => {};
}

function starsDeposit(root) {
  const cfg = S.config.stars, st = { stars: cfg.packages[1] || cfg.packages[0], busy: false };
  const wrap = h("div", { class: "col gap-lg" }); root.append(wrap);
  const ok = () => st.stars >= cfg.min && st.stars <= cfg.max;
  const sync = () => mainButton.set({ text: t("wallet.pay_stars", { n: fmt(st.stars) }), disabled: !ok() || st.busy, loading: st.busy, onClick: pay });
  function draw() {
    const inp = h("input", { class: "input num", inputmode: "numeric", value: st.stars || "", oninput: (e) => { st.stars = parseInt(e.target.value.replace(/\D/g, "")) || 0; preview(); sync(); } });
    const prev = h("div", { class: "hint ok num" });
    const preview = () => { prev.textContent = ok() ? "= " + fmt(st.stars * cfg.rate) + " GRAM" : t("wallet.stars_range", { a: fmt(cfg.min), b: fmt(cfg.max) }); prev.className = "hint num " + (ok() ? "ok" : "err"); };
    wrap.replaceChildren(
      h("div", { class: "card flat center" }, h("div", { style: { fontSize: "40px" } }, "⭐"), h("div", { class: "h3" }, t("wallet.stars_title")), h("div", { class: "small muted" }, t("wallet.stars_rate", { r: fmt(cfg.rate) }))),
      h("div", { class: "grid2" }, cfg.packages.map((n) => h("button", { class: "lang" + (st.stars === n ? " on" : ""), onclick: () => { st.stars = n; haptic.select(); draw(); } }, h("span", { class: "num" }, "⭐ " + fmt(n)), h("span", { class: "small muted num" }, short(n * cfg.rate))))),
      h("div", { class: "field" }, h("label", null, t("wallet.stars_custom")), h("div", { class: "input-wrap" }, inp, h("span", { class: "suffix" }, "⭐")), prev),
      h("div", { class: "callout" }, icon("shield", 18), h("div", null, t("wallet.stars_note"))));
    preview(); sync();
  }
  async function pay() {
    if (!ok() || st.busy) return; st.busy = true; sync();
    try {
      const r = await api.depositStars(st.stars), before = S.user.balance;
      openInvoice(r.url, async (status) => {
        if (status === "paid" || status === "unknown") {
          haptic.ok(); toast(t("wallet.stars_wait"), "ok");
          for (let i = 0; i < 8; i++) {
            await new Promise((x) => setTimeout(x, 1500));
            try { const b = await api.bootstrap(); if (b.user.balance > before) { patchUser({ balance: b.user.balance }); confetti(60); toast("+" + fmt(r.gram) + " GRAM", "ok"); break; } } catch (_) {}
          }
        } else if (status === "failed") toast(t("err.generic"), "err");
        st.busy = false; sync();
      });
    } catch (e) { fail(e); st.busy = false; sync(); }
  }
  draw();
  return () => mainButton.hide();
}

function localDeposit(root) {
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
  const R = S.config.rules, u = S.user, cfg = S.config, T = cfg.ton || {};
  const avail = Math.max(0, u.balance - (u.locked || 0));
  const lvlOk = u.level >= R.withdraw_min_level;
  // channel owners can cash out in TON at any level
  const methods = lvlOk ? cfg.withdraw.methods : (u.is_publisher && cfg.withdraw.methods.includes("ton") ? ["ton"] : []);
  if (!methods.length) {
    const need = R.withdraw_min_level - u.level, xpNeed = R.withdraw_min_level * R.xp_per_level - R.xp_per_level - u.xp;
    root.append(h("div", { class: "card center", style: { padding: "26px 18px" } },
      h("div", { style: { fontSize: "46px" } }, "🔒"), h("div", { class: "h2", style: { margin: "4px 0" } }, t("wallet.wd_locked", { n: R.withdraw_min_level })),
      h("div", { class: "muted small" }, t("wallet.wd_locked_sub", { n: R.withdraw_min_level })),
      h("div", { class: "bar", style: { margin: "18px 0 6px", height: "10px" } }, h("i", { style: { width: Math.min(100, (u.level / R.withdraw_min_level) * 100) + "%" } })),
      h("div", { class: "row between small" }, h("span", { class: "num" }, t("home.level", { n: u.level })), h("span", { class: "muted num" }, t("wallet.wd_levels_left", { n: need, xp: fmt(xpNeed) })), h("span", { class: "num" }, t("home.level", { n: R.withdraw_min_level })))),
      h("div", { class: "callout", style: { marginTop: "12px" } }, icon("info", 18), h("div", null, t("wallet.wd_tip"))));
    return;
  }
  const st = { amount: 0, method: methods[0], details: "", busy: false };
  const lim = () => st.method === "ton"
    ? { min: T.wd_min_gram || R.withdraw_min, max: Math.min((T.wd_max_usd || 1000) * (T.gram_per_usd || 1), avail, u.earned_balance ?? avail) }
    : { min: R.withdraw_min, max: Math.min(R.withdraw_max, avail) };
  st.amount = lim().max >= lim().min ? Math.floor(lim().max) : 0;
  const wrap = h("div", { class: "col gap-lg" }); root.append(wrap);
  const ok = () => { const l = lim(); return st.amount >= l.min && st.amount <= l.max && st.details.trim().length >= 5; };
  const sync = () => mainButton.set({ text: t("wallet.request_wd") + (st.amount ? " · " + short(st.amount) : ""), disabled: !ok() || st.busy, loading: st.busy, onClick: submit });
  function draw() {
    const amount = h("input", { class: "input num", inputmode: "numeric", value: st.amount || "", oninput: (e) => { st.amount = parseInt(e.target.value.replace(/\D/g, "")) || 0; hint(); sync(); } });
    const hintEl = h("div", { class: "hint" });
    const hint = () => { const l = lim(), bad = st.amount && (st.amount < l.min || st.amount > l.max); hintEl.className = "hint " + (bad ? "err" : "");
      hintEl.textContent = t("wallet.wd_range", { a: fmt(l.min), b: fmt(Math.max(0, Math.floor(l.max))) }) + (st.method === "ton" && st.amount ? "  ·  ≈ $" + (st.amount / (T.gram_per_usd || 1)).toFixed(2) : ""); amount.classList.toggle("err", !!bad); };
    wrap.replaceChildren(
      st.method === "ton" && !lvlOk && h("div", { class: "callout" }, icon("star", 18), h("div", null, t("wallet.wd_ton_pub"))),
      h("div", { class: "field" }, h("label", null, h("span", null, t("wallet.amount")), h("a", { class: "link", onclick: () => { st.amount = Math.max(0, Math.floor(lim().max)); draw(); } }, "MAX")), h("div", { class: "input-wrap" }, amount, h("span", { class: "suffix" }, "GRAM")), hintEl),
      h("div", { class: "field" }, h("label", null, t("wallet.wd_method")), h("div", { class: "col", style: { gap: "8px" } }, methods.map((k) => h("button", { class: "lang" + (st.method === k ? " on" : ""), onclick: () => { st.method = k; st.amount = Math.max(0, Math.floor(lim().max)); haptic.select(); draw(); } }, WD[k], st.method === k && icon("check", 18))))),
      h("div", { class: "field" }, h("label", null, t("wallet.wd_details")), h("input", { class: "input", placeholder: st.method === "ton" ? t("wallet.wd_ton_addr_ph") : t("wallet.wd_details_ph"), value: st.details, autocomplete: "off", autocapitalize: "none", oninput: (e) => { st.details = e.target.value; sync(); } })),
      h("div", { class: "callout warn" }, icon("clock", 18), h("div", null, st.method === "ton" ? t("wallet.wd_ton_note", { a: fmt(T.wd_min_usd), b: fmt(T.wd_max_usd) }) : t("wallet.wd_note", { n: R.retention_days }))));
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
