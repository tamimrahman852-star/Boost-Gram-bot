import { h, icon, fmt, short, countUp, ring, avatarFor, ago, skeleton } from "../ui.js";
import { t } from "../i18n.js";
import { api } from "../api.js";
import { S, TYPES, TX, levelProgress, levelEmoji, subscribe } from "../state.js";
import { haptic } from "../tg.js";
import { openTask } from "./earn.js";

export function userAvatar(u, size = "") {
  if (u.photo_url) return h("div", { class: "avatar " + size }, h("img", { src: u.photo_url, alt: "", referrerpolicy: "no-referrer" }));
  return avatarFor(u.first_name || "U", size);
}

function greeting() {
  const hr = new Date().getHours();
  return t(hr < 5 ? "home.night" : hr < 12 ? "home.morning" : hr < 18 ? "home.afternoon" : "home.evening");
}

export async function render({ root, go, push }) {
  const u = S.user, c = S.config;
  const rate = c.deposit.rate;

  // header
  root.append(h("div", { class: "top" },
    userAvatar(u),
    h("div", { class: "grow" },
      h("div", { class: "small muted" }, greeting()),
      h("div", { class: "h2 ellipsis" }, u.first_name, u.is_premium ? " ⭐" : "")),
    h("button", { class: "iconbtn", "aria-label": t("tab.me"), onclick: () => { haptic.tap(); go("me"); } }, icon("sliders", 20)),
  ));

  // hero balance
  const balEl = h("span", { class: "num" }, "0");
  const prog = levelProgress(u);
  const hero = h("div", { class: "hero" },
    h("div", { class: "row" },
      h("div", null,
        h("div", { class: "label" }, icon("sparkles", 15), t("home.balance")),
        h("div", { class: "bal" }, balEl, h("small", null, "GRAM")),
        h("div", { class: "sub num" }, "≈ ৳" + fmt(u.balance / rate))),
      h("div", { class: "lvl" }, ring(prog, 66, 7, String(u.level)))),
    h("div", { class: "row small", style: { marginTop: "10px", opacity: .9 } },
      h("span", { class: "pill" }, levelEmoji(u.level) + " " + t("home.level", { n: u.level })),
      h("span", { class: "num" }, `${fmt(u.xp - u.current_level_xp)} / ${fmt(u.next_level_xp - u.current_level_xp)} XP`)),
    h("div", { class: "actions" },
      action("bolt", t("tab.earn"), () => go("earn")),
      action("megaphone", t("tab.promote"), () => go("promote")),
      action("plus", t("home.deposit"), () => go("wallet", { seg: "deposit" }))),
  );
  root.append(hero);
  countUp(balEl, u.balance);

  // daily limit
  const d = S.daily, pct = Math.min(100, (d.used / d.max) * 100);
  root.append(h("div", { class: "card flat mt", style: { padding: "14px 16px" } },
    h("div", { class: "row between small" }, h("b", null, t("home.daily")), h("span", { class: "muted num" }, `${d.used} / ${d.max}`)),
    h("div", { class: "bar" }, h("i", { style: { width: "0%" } }))));
  requestAnimationFrame(() => requestAnimationFrame(() => { const i = root.querySelector(".bar > i"); if (i) i.style.width = pct + "%"; }));

  // stats
  const stat = (ic, cls, v, k) => h("div", { class: "stat" }, h("div", { class: "ico " + cls }, icon(ic, 18)), h("div", { class: "v num" }, v), h("div", { class: "k" }, k));
  root.append(h("div", { class: "grid2 mt" },
    stat("trend", "g2", short(u.total_earned), t("home.earned")),
    stat("check", "g1", fmt(u.completed_tasks), t("home.tasks_done")),
    stat("users", "g4", short(u.referral_earnings), t("home.ref_earned")),
    stat("lock", "g5", short(u.locked || 0), t("home.locked"))));

  // top tasks
  const hot = h("div", null, ...skeleton(2, 76));
  root.append(h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("home.hot")), h("a", { class: "link", onclick: () => go("earn") }, t("common.see_all"))), hot));
  api.tasks("all").then((r) => {
    const top = r.items.filter((x) => x.state === "new").sort((a, b) => b.reward - a.reward).slice(0, 3);
    hot.replaceChildren(...top.map((tk) => miniTask(tk, () => openTask(tk, { onDone: () => go("home") }))));
    if (!top.length) hot.append(h("div", { class: "empty" }, t("earn.empty")));
  }).catch(() => hot.replaceChildren());

  // invite banner
  root.append(h("div", { class: "card tap section", style: { background: "linear-gradient(135deg, rgba(255,92,168,.25), rgba(124,92,255,.25))" }, onclick: () => push("invite") },
    h("div", { class: "row" },
      h("div", { class: "ico g4" }, icon("gift", 22)),
      h("div", { class: "grow" }, h("div", { class: "h3" }, t("home.invite_title")), h("div", { class: "small muted" }, t("home.invite_sub", { n: fmt(c.referral.base) }))),
      icon("chevR", 20))));

  // activity
  const act = h("div", { class: "card flat" }, ...skeleton(3, 40));
  root.append(h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("home.activity")), h("a", { class: "link", onclick: () => push("history") }, t("common.see_all"))), act));
  api.history().then((r) => {
    const items = r.items.slice(0, 4);
    act.replaceChildren(items.length ? h("div", { class: "list" }, ...items.map((x) => txRow(x))) : h("div", { class: "empty" }, t("wallet.no_history")));
  }).catch(() => {});

  return subscribe(() => {});
}

function action(ic, label, fn) {
  return h("button", { class: "act", onclick: () => { haptic.tap(); fn(); } }, icon(ic, 22), label);
}

export function miniTask(tk, onTap) {
  const m = TYPES[tk.type];
  return h("div", { class: "card tap flat", style: { marginBottom: "10px", padding: "12px" }, onclick: onTap },
    h("div", { class: "row" },
      h("div", { class: "ico " + m.cls }, icon(m.icon, 20)),
      h("div", { class: "grow" }, h("div", { class: "h3 ellipsis" }, tk.title), h("div", { class: "small muted" }, t("type." + tk.type))),
      h("div", { class: "amt pos num" }, "+" + fmt(tk.reward))));
}

export function txRow(x) {
  const m = TX[x.type] || TX.task_reward;
  return h("div", { class: "item" },
    h("div", { class: "ico " + m.cls }, icon(m.icon, 19)),
    h("div", { class: "grow" }, h("div", { class: "h3 ellipsis", style: { fontSize: "14px" } }, x.title || t("tx." + x.type)), h("div", { class: "tiny muted" }, t("tx." + x.type), " · ", ago(x.created_at, t))),
    h("div", { class: "amt num " + (x.amount >= 0 ? "pos" : "neg") }, (x.amount >= 0 ? "+" : "−") + fmt(Math.abs(x.amount))));
}
