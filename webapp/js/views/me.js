import { h, icon, fmt, short, sheet, toast } from "../ui.js";
import { t, setLang, getLang } from "../i18n.js";
import { api } from "../api.js";
import { S, levelProgress, levelEmoji } from "../state.js";
import { haptic, openLink, store } from "../tg.js";
import { userAvatar } from "./home.js";
import { switchRow } from "./promote.js";
import { applyTheme, getTheme, rerender } from "../app.js";

const BADGES = [
  ["🌱", "badge.first", (u) => u.completed_tasks >= 1], ["⚡", "badge.grinder", (u) => u.completed_tasks >= 100], ["🏆", "badge.veteran", (u) => u.completed_tasks >= 1000],
  ["🔥", "badge.lvl15", (u) => u.level >= 15], ["💎", "badge.lvl30", (u) => u.level >= 30], ["💰", "badge.million", (u) => u.total_earned >= 1e6],
  ["🤝", "badge.recruiter", (u) => u.referral_earnings > 0], ["⭐", "badge.premium", (u) => u.is_premium], ["🚀", "badge.withdrawn", (u) => u.total_withdrawn > 0],
];

export async function render({ root, push }) {
  const u = S.user;
  const prog = levelProgress(u);
  root.append(h("div", { class: "pagehead" }, h("div", { class: "h1" }, t("tab.me"))));
  root.append(h("div", { class: "card center", style: { padding: "22px 16px" } },
    h("div", { style: { display: "grid", placeItems: "center" } }, userAvatar(u, "lg")),
    h("div", { class: "h1", style: { marginTop: "10px", fontSize: "22px" } }, u.first_name, u.is_premium ? " ⭐" : ""),
    u.username && h("div", { class: "small muted" }, "@" + u.username),
    h("div", { class: "row", style: { justifyContent: "center", margin: "12px 0 8px" } }, h("span", { class: "tag prem" }, levelEmoji(u.level) + " " + t("home.level", { n: u.level })), h("span", { class: "tag info num" }, fmt(u.xp) + " XP")),
    h("div", { class: "bar", style: { height: "8px" } }, h("i", { style: { width: Math.round(prog * 100) + "%" } })),
    h("div", { class: "tiny muted", style: { marginTop: "6px" } }, t("me.to_next", { n: fmt(u.next_level_xp - u.xp), l: u.level + 1 }))));

  root.append(h("div", { class: "grid3 mt" },
    mini(short(u.total_earned), t("home.earned")), mini(fmt(u.completed_tasks), t("home.tasks_done")), mini(short(u.total_withdrawn), t("me.withdrawn"))));

  root.append(h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("me.badges")), h("span", { class: "small muted num" }, BADGES.filter((b) => b[2](u)).length + "/" + BADGES.length)),
    h("div", { class: "badge-grid" }, BADGES.map(([e, k, f]) => h("div", { class: "badge" + (f(u) ? " on" : "") }, h("div", { class: "e" }, e), h("div", { class: "n" }, t(k)))))));

  // settings
  const themeSeg = h("div", { class: "seg" });
  const drawTheme = () => themeSeg.replaceChildren(...[["auto", t("me.theme_auto")], ["dark", t("me.theme_dark")], ["light", t("me.theme_light")]].map(([k, l]) => h("button", { class: getTheme() === k ? "on" : "", onclick: () => { applyTheme(k); haptic.select(); drawTheme(); } }, l)));
  drawTheme();
  root.append(h("div", { class: "section" }, h("div", { class: "head" }, h("div", { class: "h2" }, t("me.settings"))),
    h("div", { class: "col" },
      h("div", { class: "card tap flat", onclick: () => langSheet() }, h("div", { class: "row" }, h("div", { class: "ico g5" }, icon("globe", 19)), h("div", { class: "grow" }, h("div", { class: "h3" }, t("me.language")), h("div", { class: "small muted" }, S.config.langs[u.language] || u.language)), icon("chevR", 18))),
      h("div", { class: "card flat" }, h("div", { class: "h3", style: { marginBottom: "10px" } }, t("me.theme")), themeSeg),
      switchRow("bell", t("me.notifications"), t("me.notifications_sub"), u.notifications, async (v) => { u.notifications = v; try { await api.saveSettings({ notifications: v }); } catch (_) {} }),
      switchRow("sparkles", t("me.haptics"), t("me.haptics_sub"), localStorage.getItem("bg:haptics") !== "0", (v) => { localStorage.setItem("bg:haptics", v ? "1" : "0"); store.set("haptics", v ? "1" : "0"); import("../tg.js").then((m) => m.haptic.enable(v)); }),
      h("div", { class: "card tap flat", onclick: () => push("invite") }, h("div", { class: "row" }, h("div", { class: "ico g4" }, icon("gift", 19)), h("div", { class: "grow" }, h("div", { class: "h3" }, t("invite.title")), h("div", { class: "small muted" }, t("me.invite_sub"))), icon("chevR", 18))),
      h("div", { class: "card tap flat", onclick: () => openLink("https://t.me/" + S.config.bot_username) }, h("div", { class: "row" }, h("div", { class: "ico g1" }, icon("send", 19)), h("div", { class: "grow" }, h("div", { class: "h3" }, t("me.open_bot")), h("div", { class: "small muted" }, "@" + S.config.bot_username)), icon("chevR", 18))))));
  const L = S.config.legal || {}, base = (location.origin + location.pathname).replace(/[^/]*$/, ""), lang = getLang() === "bn" ? "?lang=bn" : "";
  root.append(h("div", { class: "section" }, h("div", { class: "card flat", style: { padding: "2px 14px" } }, h("div", { class: "list" },
    [["shield", t("legal.privacy"), L.privacy || "privacy.html"], ["info", t("legal.terms"), L.terms || "terms.html"]].map(([ic, label, page]) =>
      h("div", { class: "item tap", onclick: () => openLink(base + page + lang) }, h("div", { class: "ico g5" }, icon(ic, 18)), h("div", { class: "grow h3", style: { fontSize: "14px" } }, label), icon("chevR", 18))),
    h("div", { class: "item tap", onclick: () => openLink("https://telegram.org/privacy") }, h("div", { class: "ico g1" }, icon("send", 18)), h("div", { class: "grow h3", style: { fontSize: "14px" } }, t("legal.tg_privacy")), icon("chevR", 18)),
    h("div", { class: "item tap", onclick: () => openLink("https://telegram.org/tos") }, h("div", { class: "ico g1" }, icon("send", 18)), h("div", { class: "grow h3", style: { fontSize: "14px" } }, t("legal.tg_terms")), icon("chevR", 18))))));
  root.append(h("div", { class: "center tiny faint", style: { margin: "18px 0 8px" } }, "Boost Gram Mini App · v1.1 · " + t("legal.not_affiliated")));
}

const mini = (v, k) => h("div", { class: "stat center" }, h("div", { class: "v num" }, v), h("div", { class: "k" }, k));

function langSheet() {
  sheet((s) => {
    const cur = S.user.language;
    s.render(h("div", { class: "h2", style: { marginBottom: "6px" } }, t("me.language")), h("div", { class: "small muted", style: { marginBottom: "14px" } }, t("me.language_sub")),
      h("div", { class: "lang-grid" }, Object.entries(S.config.langs).map(([code, name]) => h("button", { class: "lang" + (code === cur ? " on" : ""), onclick: async () => {
        haptic.select(); S.user.language = code;
        try { await api.saveSettings({ language: code }); } catch (_) {}
        const applied = await setLang(code); s.close();
        toast(applied ? name : t("me.lang_bot_only", { l: name }), "ok");
        rerender();
      } }, name, code === cur && icon("check", 17)))));
  });
}
