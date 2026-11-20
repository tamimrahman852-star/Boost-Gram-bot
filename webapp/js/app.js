import { h, icon, toast, closeAllSheets, hasSheet } from "./ui.js";
import { t, setLang, getLang } from "./i18n.js";
import { api, demo } from "./api.js";
import { S, subscribe } from "./state.js";
import * as tgw from "./tg.js";
import * as home from "./views/home.js";
import * as earn from "./views/earn.js";
import * as promote from "./views/promote.js";
import * as wallet from "./views/wallet.js";
import * as invite from "./views/invite.js";
import * as me from "./views/me.js";
import * as ads from "./views/ads.js";

const VIEWS = { home, earn, promote, wallet, me, invite, ads, history: { render: wallet.history }, adaddchannel: { render: ads.addChannel }, adchannel: { render: ads.channel }, adcreate: { render: ads.create }, adcampaign: { render: ads.campaign } };
const TABS = [["home", "home"], ["earn", "bolt"], ["promote", "megaphone"], ["ads", "trend"], ["wallet", "wallet"], ["me", "user"]];
const START_MAP = { earn: "earn", promote: "promote", ads: "ads", wallet: "wallet", invite: "invite", deposit: "wallet" };

const viewEl = () => document.getElementById("view");
let stack = [];        // navigation stack of {name, params, tab}
let cleanup = null;
let navId = 0;

// ---------------- theme ----------------
let themePref = "auto";
export const getTheme = () => themePref;
export function applyTheme(pref) {
  themePref = pref; tgw.store.set("theme", pref);
  const scheme = pref === "auto" ? tgw.colorScheme() : pref;
  document.documentElement.dataset.theme = scheme;
  tgw.setChrome(scheme === "light" ? "#f2f4fb" : "#070912");
}

// ---------------- navigation ----------------
export function navigate(name, params = {}, { push = false } = {}) {
  const isTab = TABS.some((x) => x[0] === name);
  const prevTab = stack.length ? stack[stack.length - 1].tab : "home";
  const entry = { name, params, tab: isTab ? name : prevTab };
  if (isTab || !push) stack = [entry]; else stack.push(entry);
  show(entry);
}
export const rerender = () => { buildTabbar(); if (stack.length) show(stack[stack.length - 1]); };
function popBack() {
  if (hasSheet()) { closeAllSheets(); return; }
  if (stack.length > 1) { stack.pop(); show(stack[stack.length - 1]); }
  else if (!TABS.some((x) => x[0] === stack[0].name)) navigate("home");
}

async function show(entry) {
  const my = ++navId;
  cleanup && cleanup(); cleanup = null;
  tgw.mainButton.hide(); closeAllSheets();
  const root = h("div", { class: "page" });
  viewEl().replaceChildren(root); window.scrollTo(0, 0);
  document.querySelectorAll(".tab").forEach((el) => el.classList.toggle("on", el.dataset.tab === entry.tab));
  const isSub = !TABS.some((x) => x[0] === entry.name);
  isSub ? tgw.back.show(popBack) : tgw.back.hide();
  try {
    const c = await VIEWS[entry.name].render({ root, params: entry.params, go: (n, p) => navigate(n, p), push: (n, p) => navigate(n, p, { push: true }), back: popBack, replace: (n, p) => { stack.pop(); navigate(n, p, { push: true }); } });
    if (my !== navId) { c && c(); return; }
    cleanup = c || null;
    if (isSub && !tgw.inTelegram) {
      const head = root.querySelector(".pagehead");
      head && head.prepend(h("button", { class: "iconbtn backbtn", "aria-label": t("common.back"), onclick: () => { tgw.haptic.tap(); popBack(); } }, icon("chevL", 20)));
    }
  } catch (e) {
    console.error(e);
    root.replaceChildren(h("div", { class: "empty" }, h("div", { class: "big" }, "⚠️"), h("div", { class: "h3" }, t("err.generic")), h("div", { class: "mt" }, h("button", { class: "btn sm", onclick: () => show(entry) }, t("common.retry")))));
  }
}

function buildTabbar() {
  const bar = document.getElementById("tabbar");
  bar.replaceChildren(...TABS.map(([name, ic]) => {
    const b = h("button", { class: "tab", dataset: { tab: name }, "aria-label": t("tab." + name), onclick: () => { tgw.haptic.select(); navigate(name); } }, icon(ic, 23), t("tab." + name));
    if (name === "promote" && S.badges.reviews > 0) b.append(h("span", { class: "dot" }, String(S.badges.reviews)));
    return b;
  }));
  const cur = stack.length ? stack[stack.length - 1].tab : "home";
  bar.querySelectorAll(".tab").forEach((el) => el.classList.toggle("on", el.dataset.tab === cur));
}

// ---------------- boot ----------------
async function boot() {
  tgw.initTelegram();
  const [theme, lang, hap] = await Promise.all([tgw.store.get("theme"), tgw.store.get("lang"), tgw.store.get("haptics")]);
  applyTheme(theme || "auto");
  tgw.onThemeChange(() => themePref === "auto" && applyTheme("auto"));
  tgw.haptic.enable(hap !== "0");
  if (lang) await setLang(lang);

  window.addEventListener("offline", () => { const el = h("div", { class: "offline", id: "off" }, t("err.offline")); document.body.append(el); });
  window.addEventListener("online", () => document.getElementById("off")?.remove());
  if (demo) document.body.append(h("div", { class: "demo" }, "DEMO"));

  let data;
  try { data = await api.bootstrap(tgw.startParam); }
  catch (e) {
    document.getElementById("splash").innerHTML = `<div class="empty"><div class="big">📡</div><div class="h3">${t("err.generic")}</div><button class="btn sm" style="margin:14px auto 0" onclick="location.reload()">${t("common.retry")}</button></div>`;
    return;
  }
  S.user = data.user; S.config = data.config; S.daily = data.daily; S.badges = data.badges || { reviews: 0 };
  if (!lang && S.user.language) await setLang(S.user.language, false);
  S.startParam = tgw.startParam;

  const q = new URLSearchParams(location.search);
  const start = q.get("tab") || START_MAP[(tgw.startParam || "").split("_")[0]] || "home";
  document.getElementById("splash").classList.add("gone");
  setTimeout(() => document.getElementById("splash")?.remove(), 500);
  buildTabbar();
  navigate(VIEWS[start] ? start : "home", q.has("seg") ? { seg: q.get("seg") } : q.has("ptab") ? { tab: q.get("ptab") } : {});
  subscribe(() => buildTabbar());
}
boot();
