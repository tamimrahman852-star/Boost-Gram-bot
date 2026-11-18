// Thin wrapper around Telegram.WebApp with safe fallbacks for normal browsers (demo mode).
const W = window.Telegram && window.Telegram.WebApp;

export const inTelegram = !!(W && W.initData);
export const initData = inTelegram ? W.initData : "";
export const tgUser = (W && W.initDataUnsafe && W.initDataUnsafe.user) || null;
export const startParam = (W && W.initDataUnsafe && W.initDataUnsafe.start_param) || new URLSearchParams(location.search).get("start") || "";

const call = (fn, ...a) => { try { return fn && fn.apply(W, a); } catch (_) { /* old client */ } };

let hapticsOn = true;
export const haptic = {
  enable(v) { hapticsOn = v; },
  tap() { hapticsOn && call(W?.HapticFeedback?.impactOccurred?.bind(W.HapticFeedback), "light"); },
  press() { hapticsOn && call(W?.HapticFeedback?.impactOccurred?.bind(W.HapticFeedback), "medium"); },
  select() { hapticsOn && call(W?.HapticFeedback?.selectionChanged?.bind(W.HapticFeedback)); },
  ok() { hapticsOn && call(W?.HapticFeedback?.notificationOccurred?.bind(W.HapticFeedback), "success"); },
  warn() { hapticsOn && call(W?.HapticFeedback?.notificationOccurred?.bind(W.HapticFeedback), "warning"); },
  err() { hapticsOn && call(W?.HapticFeedback?.notificationOccurred?.bind(W.HapticFeedback), "error"); },
};

export function initTelegram() {
  if (!W) return;
  call(W.ready); call(W.expand);
  call(W.disableVerticalSwipes);
  call(W.setHeaderColor, "bg_color");
  call(W.setBackgroundColor, "bg_color");
  call(W.enableClosingConfirmation);
}

export function colorScheme() {
  return (W && W.colorScheme) || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
}
export function onThemeChange(cb) { W && W.onEvent && W.onEvent("themeChanged", cb); }

export function setChrome(bg) {
  call(W && W.setHeaderColor, bg); call(W && W.setBackgroundColor, bg); call(W && W.setBottomBarColor, bg);
}

// ---------- Back button ----------
let backHandler = null;
export const back = {
  show(cb) {
    if (!W || !W.BackButton) return;
    if (backHandler) W.BackButton.offClick(backHandler);
    backHandler = cb; W.BackButton.onClick(cb); W.BackButton.show();
  },
  hide() {
    if (!W || !W.BackButton) return;
    if (backHandler) W.BackButton.offClick(backHandler);
    backHandler = null; W.BackButton.hide();
  },
};

// ---------- Main action button: Telegram MainButton or in-page fallback ----------
let mbHandler = null;
let bar = null;
export const mainButton = {
  set({ text, onClick, disabled = false, loading = false, color }) {
    this.last = { text, onClick, disabled, loading };
    if (inTelegram && W.MainButton) {
      const mb = W.MainButton;
      if (mbHandler) mb.offClick(mbHandler);
      mbHandler = onClick; mb.onClick(onClick);
      mb.setParams({ text, is_active: !disabled && !loading, is_visible: true, color: color || "#7c5cff", text_color: "#ffffff" });
      loading ? mb.showProgress(false) : mb.hideProgress();
    } else {
      bar = bar || document.getElementById("actionbar");
      bar.innerHTML = "";
      const b = document.createElement("button");
      b.className = "btn shine"; b.textContent = text; b.disabled = disabled || loading;
      if (loading) { b.textContent = ""; const s = document.createElement("span"); s.className = "spin"; b.append(s); }
      b.onclick = onClick; bar.append(b); bar.classList.add("on");
    }
    document.getElementById("view")?.classList.add("has-action");
  },
  hide() {
    if (inTelegram && W.MainButton) { if (mbHandler) W.MainButton.offClick(mbHandler); mbHandler = null; W.MainButton.hide(); }
    else { bar = bar || document.getElementById("actionbar"); bar && bar.classList.remove("on"); }
    document.getElementById("view")?.classList.remove("has-action");
  },
};

// ---------- Helpers ----------
export function openTelegramLink(url) {
  if (W && W.openTelegramLink && /^https:\/\/t\.me\//i.test(url)) return W.openTelegramLink(url);
  window.open(url, "_blank", "noopener");
}
export function openLink(url) { (W && W.openLink) ? W.openLink(url) : window.open(url, "_blank", "noopener"); }
export function shareLink(url, text) {
  openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text || "")}`);
}
export function confirmDialog(message) {
  return new Promise((res) => {
    if (W && W.showConfirm) W.showConfirm(message, (ok) => res(!!ok));
    else res(window.confirm(message));
  });
}
export async function copyText(txt) {
  try { await navigator.clipboard.writeText(txt); return true; }
  catch (_) {
    const t = document.createElement("textarea"); t.value = txt; document.body.append(t); t.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch (_) {} t.remove(); return ok;
  }
}

// Persistent per-user storage (Telegram CloudStorage, falls back to localStorage)
export const store = {
  get(key) {
    return new Promise((res) => {
      if (W && W.CloudStorage && inTelegram) W.CloudStorage.getItem(key, (e, v) => res(e ? null : v || null));
      else res(localStorage.getItem("bg:" + key));
    });
  },
  set(key, val) {
    if (W && W.CloudStorage && inTelegram) W.CloudStorage.setItem(key, String(val));
    else localStorage.setItem("bg:" + key, String(val));
  },
};
