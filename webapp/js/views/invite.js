import { h, icon, fmt, short, skeleton, toast, ago, avatarFor, confetti } from "../ui.js";
import { t } from "../i18n.js";
import { api } from "../api.js";
import { S, fail } from "../state.js";
import { haptic, copyText, shareLink } from "../tg.js";

export async function render({ root }) {
  const cfg = S.config;
  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("invite.title"))));
  const body = h("div", null, ...skeleton(3, 120));
  root.append(body);
  let r;
  try { r = await api.referrals(); } catch (e) { fail(e); body.replaceChildren(h("div", { class: "empty" }, t("err.generic"))); return; }

  const tiers = h("div", { class: "col" }, r.tiers.map((x) => h("div", { class: "tier" },
    h("div", { class: "pc gold-text num" }, x.percent + "%"),
    h("div", { class: "grow" }, h("div", { class: "h3", style: { fontSize: "14px" } }, t("invite.tier", { n: x.level })), h("div", { class: "small muted" }, t("invite.tier_sub_" + x.level))),
    h("div", { style: { textAlign: "right" } }, h("div", { class: "h3 num" }, fmt(x.count)), h("div", { class: "tiny muted num" }, "+" + short(x.earned))))));

  body.replaceChildren(
    h("div", { class: "hero", style: { background: "linear-gradient(140deg, #ff5ca8, #7c5cff 70%)" } },
      h("div", { style: { fontSize: "40px" } }, "🎁"),
      h("div", { class: "h1", style: { color: "#fff", margin: "4px 0" } }, t("invite.hero", { n: fmt(cfg.referral.base) })),
      h("div", { style: { opacity: .9 } }, t("invite.hero_sub")),
      h("div", { class: "grid2", style: { marginTop: "16px" } },
        h("div", { class: "pill", style: { justifyContent: "center", padding: "10px" } }, "👥 " + fmt(r.count) + " " + t("invite.friends")),
        h("div", { class: "pill", style: { justifyContent: "center", padding: "10px" } }, "💰 " + short(r.earnings) + " GRAM"))),
    h("div", { class: "card mt" },
      h("div", { class: "small muted", style: { marginBottom: "8px", fontWeight: 700 } }, t("invite.your_link")),
      h("div", { class: "code" }, r.link),
      h("div", { class: "grid2", style: { marginTop: "12px" } },
        h("button", { class: "btn sec", onclick: async () => { await copyText(r.link); haptic.ok(); toast(t("common.copied")); } }, icon("copy", 18), t("common.copy")),
        h("button", { class: "btn shine", onclick: () => { haptic.press(); shareLink(r.link, t("invite.share_text")); } }, icon("share", 18), t("common.share")))),
    h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("invite.tiers"))), tiers),
    h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("invite.how"))),
      h("div", { class: "card flat" }, h("ol", { class: "steps", style: { margin: 0, padding: 0 } }, ["invite.h1", "invite.h2", "invite.h3"].map((k) => h("li", null, t(k, { n: fmt(cfg.referral.base) })))))),
    h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("invite.recent"))),
      r.recent.length ? h("div", { class: "card flat", style: { padding: "2px 14px" } }, h("div", { class: "list" }, r.recent.map((x) => h("div", { class: "item" }, avatarFor(x.name, "sm"), h("div", { class: "grow" }, h("div", { class: "h3", style: { fontSize: "14px" } }, x.name), h("div", { class: "tiny muted" }, ago(x.joined, t))), h("div", { class: "amt pos num" }, "+" + fmt(x.earned)))))) : h("div", { class: "empty" }, t("invite.none"))));
}
