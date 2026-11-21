// Perks (daily streak, Lucky Spin, VIP / Boost passes) and the advertiser ROI dashboard.
import { h, icon, fmt, short, sheet, toast, confetti, skeleton, sleep } from "../ui.js";
import { t } from "../i18n.js";
import { api } from "../api.js";
import { S, fail, patchUser } from "../state.js";
import { haptic } from "../tg.js";

const G = () => S.config.growth || {};
const until = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });

// ======================== Perks ========================
export async function render({ root }) {
  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("perk.title")), h("div", { class: "small muted" }, t("perk.sub"))));
  const body = h("div", { class: "col gap-lg" }, ...skeleton(3, 110));
  root.append(body);
  let P = null;

  async function load() {
    try { P = await api.perks(); } catch (e) { return body.replaceChildren(h("div", { class: "empty" }, t("err.generic"))); }
    draw();
  }
  function draw() {
    body.replaceChildren(streakCard(), spinCard(), passCard("vip"), passCard("boost"));
  }

  function streakCard() {
    const days = P.streak % 7 || (P.streak && P.claimed_today ? 7 : 0);
    const dots = h("div", { class: "row", style: { gap: "6px", margin: "12px 0" } }, ...Array.from({ length: 7 }, (_, i) =>
      h("div", { style: { flex: 1, height: "8px", borderRadius: "6px", background: i < days ? "var(--gold, #ffc24a)" : "var(--line, #ffffff22)" } })));
    const btn = h("button", { class: "btn", disabled: P.claimed_today, onclick: async () => {
      btn.disabled = true;
      try {
        const r = await api.streak(); haptic.ok(); patchUser(r.user); P.streak = r.streak; P.claimed_today = true; P.free_spin = P.free_spin || r.free_spin;
        toast(t("perk.streak_ok", { n: r.xp }), "ok"); if (r.free_spin) { confetti(60); toast(t("perk.free_spin_won"), "ok"); }
        draw();
      } catch (e) { fail(e); btn.disabled = false; }
    } }, P.claimed_today ? t("perk.claimed") : t("perk.claim"));
    return h("div", { class: "card" }, h("div", { class: "row" }, h("div", { class: "ico g4" }, icon("bolt", 20)), h("div", { class: "grow" },
      h("div", { class: "h3" }, t("perk.streak", { n: P.streak })), h("div", { class: "small muted" }, t("perk.streak_sub")))), dots, btn);
  }

  function spinCard() {
    const out = h("div", { class: "big num gold-text center", style: { fontSize: "34px", margin: "10px 0", minHeight: "44px" } }, "🎰");
    const left = h("div", { class: "tiny muted center" }, t("perk.spins_left", { n: P.spins_left }));
    const btn = h("button", { class: "btn", onclick: async () => {
      btn.disabled = true; haptic.press(); out.textContent = "🎰 …";
      try {
        const [r] = await Promise.all([api.spin(), sleep(700)]);
        patchUser(r.user); P.free_spin = false; P.spins_left = Math.max(0, P.spins_left - 1);
        out.textContent = r.prize ? "+" + fmt(r.prize) : "😅 0";
        if (r.prize >= 40000) { confetti(80); haptic.ok(); } else haptic.warn();
        left.textContent = t("perk.spins_left", { n: P.spins_left });
      } catch (e) { out.textContent = "🎰"; fail(e); }
      btn.disabled = P.spins_left <= 0;
    } }, P.free_spin ? t("perk.spin_free") : t("perk.spin", { n: fmt(P.spin_cost) }));
    return h("div", { class: "card" }, h("div", { class: "row" }, h("div", { class: "ico g2" }, icon("sparkles", 20)), h("div", { class: "grow" },
      h("div", { class: "h3" }, t("perk.spin_title")), h("div", { class: "small muted" }, t("perk.spin_sub", { max: fmt(Math.max(...P.prizes)) })))), out, left, h("div", { style: { marginTop: "10px" } }, btn));
  }

  function passCard(kind) {
    const cfg = G()[kind] || {}, until_ = kind === "vip" ? P.vip_until : P.boost_until;
    const btn = h("button", { class: "btn" + (until_ ? " sec" : ""), onclick: async () => {
      btn.disabled = true;
      try { const r = await api.buyPass(kind); patchUser(r.user); haptic.ok(); toast(t("perk.pass_ok"), "ok"); await load(); }
      catch (e) { fail(e); btn.disabled = false; }
    } }, (until_ ? t("perk.extend") : t("perk.buy")) + " · " + short(cfg.price) + " GRAM");
    return h("div", { class: "card" }, h("div", { class: "row" }, h("div", { class: "ico " + (kind === "vip" ? "g5" : "g1") }, icon(kind === "vip" ? "shield" : "trend", 20)), h("div", { class: "grow" },
      h("div", { class: "h3" }, t("perk." + kind), until_ && h("span", { class: "tag ok", style: { marginLeft: "8px" } }, t("perk.until", { d: until(until_) }))),
      h("div", { class: "small muted" }, t("perk." + kind + "_sub", { d: cfg.days })))), h("div", { style: { marginTop: "12px" } }, btn));
  }
  await load();
}

// ======================== Advertiser ROI dashboard ========================
export async function analytics({ root }) {
  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("roi.title")), h("div", { class: "small muted" }, t("roi.sub"))));
  const body = h("div", { class: "col gap-lg" }, ...skeleton(3, 100));
  root.append(body);
  let r;
  try { r = await api.analytics(); } catch (e) { return body.replaceChildren(h("div", { class: "empty" }, t("err.generic"))); }
  if (!r.items.length) return body.replaceChildren(h("div", { class: "empty" }, h("div", { class: "big" }, "📊"), h("div", { class: "h3" }, t("roi.none"))));
  const T = r.totals;
  body.replaceChildren(
    h("div", { class: "grid3" }, kpi(fmt(T.completions), t("roi.actions")), kpi(fmt(T.cpa), t("roi.cpa")), kpi(pct(T.drop_rate), t("roi.drop"))),
    h("div", { class: "grid3" }, kpi(short(T.spent), t("roi.spent")), kpi(short(T.remaining), t("roi.remaining")), kpi(fmt(T.campaigns), t("roi.campaigns"))),
    ...r.items.map((c) => h("div", { class: "card tap", onclick: () => detail(c) },
      h("div", { class: "row between" }, h("div", { class: "h3 ellipsis" }, c.title), h("span", { class: "tag" + (c.status === "active" ? " ok" : "") }, c.status)),
      h("div", { class: "bar gold", style: { margin: "10px 0 8px" } }, h("i", { style: { width: Math.round(c.progress * 100) + "%" } })),
      h("div", { class: "row between small muted" }, h("span", { class: "num" }, `${fmt(c.completions)} / ${fmt(c.slots)}`), h("span", null, t("roi.cpa") + " " + fmt(c.cpa)), h("span", null, t("roi.drop") + " " + pct(c.retention.drop_rate))))));
}

const pct = (x) => (Math.round((x || 0) * 1000) / 10) + "%";
const kpi = (v, k) => h("div", { class: "stat center" }, h("div", { class: "v num" }, v), h("div", { class: "k" }, k));

export async function detail(c) {
  sheet((s) => {
    const box = h("div", null, h("div", { class: "h2" }, c.title), ...skeleton(2, 90));
    s.render(box);
    api.analyticsOne(c.id).then((d) => {
      const max = Math.max(1, ...d.daily.map((x) => x.n));
      const chart = h("div", { class: "row", style: { alignItems: "flex-end", gap: "3px", height: "70px", margin: "12px 0 4px" } },
        ...d.daily.map((x) => h("div", { title: x.d + ": " + x.n, style: { flex: 1, height: Math.max(4, (x.n / max) * 70) + "px", background: "var(--gold, #ffc24a)", borderRadius: "3px" } })));
      const tg = d.targeting, tgt = [...tg.langs, ...tg.countries, tg.niche].filter(Boolean).join(" · ");
      const feat = h("div", { class: "row", style: { gap: "8px", marginTop: "12px" } }, ...(G().feature?.days || []).map((n) => h("button", { class: "btn sec grow", onclick: async () => {
        try { const r = await api.feature(d.id, n); patchUser({ balance: r.balance }); haptic.ok(); toast(t("roi.featured_ok"), "ok"); } catch (e) { fail(e); }
      } }, n + "d")));
      box.replaceChildren(h("div", { class: "h2" }, d.title),
        h("div", { class: "grid3 mt" }, kpi(fmt(d.completions), t("roi.actions")), kpi(fmt(d.cpa), t("roi.cpa")), kpi(pct(d.retention.drop_rate), t("roi.drop"))),
        h("div", { class: "small muted", style: { marginTop: "10px" } }, `${t("roi.spent")}: ${fmt(d.spent)} · ${t("roi.remaining")}: ${fmt(d.remaining)} / ${fmt(d.budget)}`),
        h("div", { class: "small muted" }, `${t("roi.retained")}: ${fmt(d.retention.verified)} · ${t("roi.pending")}: ${fmt(d.retention.pending)} · ${t("roi.dropped")}: ${fmt(d.retention.dropped)}`),
        tgt && h("div", { class: "small muted" }, "🎯 " + tgt),
        h("div", { class: "h3", style: { marginTop: "14px" } }, t("roi.last14")), chart,
        h("div", { class: "h3", style: { marginTop: "14px" } }, t("roi.feature")), h("div", { class: "small muted" }, d.featured_until ? t("roi.featured_until", { d: until(d.featured_until) }) : t("roi.feature_sub", { n: short((G().feature || {}).per_day) })), feat);
    }).catch((e) => { fail(e); s.close(); });
  });
}
