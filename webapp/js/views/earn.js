import { h, icon, fmt, skeleton, sheet, toast, confetti, countUp, compressImage, sleep } from "../ui.js";
import { t } from "../i18n.js";
import { api } from "../api.js";
import { S, TYPES, TYPE_ORDER, fail, patchUser, setDaily, levelEmoji } from "../state.js";
import { haptic, openTelegramLink } from "../tg.js";

export async function render({ root, params }) {
  let type = params.type || "all";
  let alive = true;

  const dailyPill = h("span", { class: "tag info num" });
  const syncDaily = () => { dailyPill.textContent = `${S.daily.used}/${S.daily.max} ${t("earn.today")}`; };
  syncDaily();

  root.append(h("div", { class: "pagehead" },
    h("div", { class: "h1" }, t("tab.earn")),
    dailyPill,
    h("button", { class: "iconbtn", "aria-label": "refresh", onclick: () => { haptic.tap(); load(); } }, icon("refresh", 19))));

  const chips = h("div", { class: "chips" });
  const list = h("div");
  root.append(chips, h("div", { style: { marginTop: "6px" } }, list));

  const drawChips = () => {
    chips.replaceChildren(...["all", ...TYPE_ORDER].map((k) =>
      h("button", { class: "chip" + (k === type ? " on" : ""), onclick: () => { if (type === k) return; haptic.select(); type = k; drawChips(); load(); } },
        k !== "all" && icon(TYPES[k].icon, 15), t(k === "all" ? "earn.all" : "type.s_" + k))));
    if (type !== "all") { const on = chips.querySelector(".on"); on && on.scrollIntoView({ inline: "center", block: "nearest" }); }
  };
  drawChips();

  async function load() {
    list.replaceChildren(...skeleton(4, 92));
    try {
      const r = await api.tasks(type);
      if (!alive) return;
      setDaily(r.daily); syncDaily();
      if (!r.items.length) {
        list.replaceChildren(h("div", { class: "empty" }, h("div", { class: "big" }, "🎯"), h("div", { class: "h3" }, t("earn.empty")), h("div", { class: "small" }, t("earn.empty_sub"))));
        return;
      }
      list.replaceChildren(...r.items.map((tk, i) => taskCard(tk, i, () => openTask(tk, { onDone: load }))));
    } catch (e) { list.replaceChildren(h("div", { class: "empty" }, t("err.generic"), h("div", { class: "mt" }, h("button", { class: "btn sm sec", onclick: load }, t("common.retry"))))); }
  }
  load();
  return () => { alive = false; };
}

export function taskCard(tk, i = 0, onTap) {
  const m = TYPES[tk.type];
  const locked = tk.premium && !S.user.is_premium;
  const used = tk.slots_total - tk.slots_left, pct = Math.round((used / tk.slots_total) * 100);
  return h("div", { class: "card tap task", style: { animation: `pageIn .4s ${Math.min(i, 8) * 0.04}s both var(--ease)`, opacity: locked ? .7 : 1 }, onclick: () => { haptic.tap(); onTap(); } },
    h("div", { class: "ico " + m.cls, style: { width: "46px", height: "46px", borderRadius: "15px" } }, icon(m.icon, 22)),
    h("div", { class: "body" },
      h("div", { class: "title ellipsis" }, tk.title),
      h("div", { class: "meta" },
        h("span", { class: "tag" }, t("type." + tk.type)),
        tk.premium && h("span", { class: "tag prem" }, "⭐ " + t("earn.premium")),
        tk.retention_days > 0 && h("span", { class: "tag" }, icon("clock", 11), tk.retention_days + t("time.d_short")),
        tk.state === "pending" && h("span", { class: "tag warn" }, t("earn.in_review"))),
      h("div", { class: "bar" }, h("i", { style: { width: pct + "%" } }))),
    h("div", { class: "reward" }, h("div", { class: "v num gold-text" }, "+" + fmt(tk.reward)), h("div", { class: "k" }, "GRAM"), locked && icon("lock", 14)));
}

const STEPS = {
  join: ["earn.step_open", "earn.step_verify", "earn.step_stay"],
  view: ["earn.step_view", "earn.step_wait"],
  proof: ["earn.step_open_do", "earn.step_shot", "earn.step_review"],
};

export function openTask(tk, { onDone } = {}) {
  const m = TYPES[tk.type];
  if (tk.state === "pending") return toast(t("earn.in_review_toast"), "warn");
  if (tk.premium && !S.user.is_premium) { haptic.warn(); return toast(t("err.premium_only"), "warn"); }

  sheet((s) => {
    let started = null, busy = false, blob = null, preview = null, screen = "main";

    const head = () => h("div", { class: "row", style: { marginBottom: "14px" } },
      h("div", { class: "ico " + m.cls, style: { width: "52px", height: "52px", borderRadius: "17px" } }, icon(m.icon, 26)),
      h("div", { class: "grow" }, h("div", { class: "h2 ellipsis" }, tk.title), h("div", { class: "small muted" }, "@" + tk.username + " · " + t("type." + tk.type))),
      h("div", { class: "reward" }, h("div", { class: "v num gold-text", style: { fontSize: "22px" } }, "+" + fmt(tk.reward)), h("div", { class: "k" }, "GRAM")));

    const kind = tk.type === "post_view" ? "view" : ["bot_start", "reaction"].includes(tk.type) ? "proof" : "join";

    const draw = () => {
      if (screen !== "main") return;
      const stepsDone = started ? (kind === "join" ? 1 : kind === "proof" ? (blob ? 2 : 1) : 1) : 0;
      const body = [head(),
        h("ol", { class: "steps", style: { margin: "0 0 18px", padding: 0 } }, STEPS[kind].map((k, i) => h("li", { class: i < stepsDone ? "done" : "" }, t(k, { n: tk.retention_days || 7, s: S.config.rules.view_dwell, h: S.config.rules.proof_review_hours })))),
      ];
      if (kind === "join" && tk.retention_days) body.push(h("div", { class: "callout warn", style: { marginBottom: "14px" } }, icon("warn", 18), h("div", null, t("earn.retention_warn", { n: tk.retention_days }))));
      if (tk.type === "boost_7day") body.push(h("div", { class: "callout", style: { marginBottom: "14px" } }, icon("rocket", 18), h("div", null, t("earn.boost_note"))));

      if (kind === "proof" && started) {
        const drop = h("label", { class: "drop" + (preview ? " has" : "") },
          preview ? h("img", { src: preview, alt: "" }) : [icon("image", 28), h("div", { class: "h3", style: { marginTop: "6px" } }, t("earn.pick_shot")), h("div", { class: "small" }, t("earn.shot_hint"))],
          h("input", { type: "file", accept: "image/*", class: "hide", onchange: async (ev) => {
            const f = ev.target.files[0]; if (!f) return;
            try { blob = await compressImage(f); preview = URL.createObjectURL(blob); haptic.ok(); draw(); } catch (_) { toast(t("err.bad_image"), "err"); }
          } }));
        body.push(drop);
      }

      let label, handler, cls = "btn shine";
      if (!started) { label = kind === "view" ? t("earn.view_post") : t("earn.open_in_tg"); handler = start; }
      else if (kind === "join") { label = t("earn.verify"); handler = verify; cls = "btn ok"; }
      else if (kind === "proof") { label = t("earn.submit_proof"); handler = submitProof; cls = "btn ok"; }
      const btn = h("button", { class: cls, onclick: handler, disabled: busy || (kind === "proof" && started && !blob) }, busy ? h("span", { class: "spin" }) : [kind === "join" && started ? icon("shield", 20) : icon(kind === "view" ? "eye" : "send", 20), label]);
      body.push(h("div", { style: { marginTop: "14px" } }, btn));
      if (started && kind === "join") body.push(h("button", { class: "btn sec", style: { marginTop: "10px" }, onclick: () => openTelegramLink(tk.link) }, icon("link", 18), t("earn.open_again")));
      s.render(...body);
    };

    const guard = async (fn) => { if (busy) return; busy = true; draw(); try { await fn(); } catch (e) { screen = "main"; fail(e); } finally { busy = false; if (!s.closed) draw(); } };

    const start = () => guard(async () => {
      const r = await api.startTask(tk.id);
      started = r; haptic.press();
      if (r.kind === "view") { await countdown(r); return; }
      openTelegramLink(r.url);
    });

    async function countdown(r) {
      const secs = r.dwell || 6, C = 2 * Math.PI * 52;
      screen = "count";
      const num = h("div", { class: "c num" }, String(secs));
      const wrap = h("div", { class: "timer" });
      wrap.innerHTML = `<svg width="120" height="120" viewBox="0 0 120 120"><circle cx="60" cy="60" r="52" fill="none" stroke="rgba(255,255,255,.1)" stroke-width="9"/></svg>`;
      const svgArc = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      for (const [k, v] of Object.entries({ cx: 60, cy: 60, r: 52, fill: "none", stroke: "url(#gradStroke)", "stroke-width": 9, "stroke-linecap": "round", "stroke-dasharray": C, "stroke-dashoffset": 0 })) svgArc.setAttribute(k, v);
      svgArc.style.transition = "stroke-dashoffset 1s linear";
      wrap.querySelector("svg").append(svgArc); wrap.append(num);
      s.render(head(), h("div", { class: "center" }, wrap, h("div", { class: "h3" }, t("earn.view_wait")), h("div", { class: "small muted", style: { marginTop: "4px" } }, t("earn.view_wait_sub"))));
      for (let i = secs; i > 0; i--) {
        num.textContent = String(i); svgArc.style.strokeDashoffset = C * (1 - (i - 1) / secs); haptic.select(); await sleep(1000);
        if (s.closed) return;
      }
      await verifyNow();
    }

    async function verifyNow() {
      const r = await api.verifyTask(tk.id, started && started.token);
      success(r);
    }
    const verify = () => guard(verifyNow);

    const submitProof = () => guard(async () => {
      const r = await api.submitProof(tk.id, blob);
      haptic.ok(); confetti(50); screen = "done";
      s.render(h("div", { class: "center", style: { padding: "10px 0 6px" } },
        h("div", { style: { fontSize: "60px" } }, "📨"), h("div", { class: "h2" }, t("earn.proof_sent")),
        h("div", { class: "muted", style: { margin: "6px 0 18px" } }, t("earn.proof_sent_sub", { h: r.review_hours || 48 })),
        h("button", { class: "btn", onclick: () => { s.close(); onDone && onDone(); } }, t("common.done"))));
    });

    function success(r) {
      patchUser(r.user || { balance: r.balance }); setDaily(r.daily);
      confetti(); haptic.ok(); screen = "done";
      const amt = h("div", { class: "big-reward gold-text num" }, "+0");
      s.render(h("div", { class: "center", style: { padding: "6px 0" } },
        h("div", { style: { fontSize: "56px" } }, "🎉"), h("div", { class: "h2" }, t("earn.reward_in")), amt, h("div", { class: "muted", style: { fontWeight: 700 } }, "GRAM"),
        h("div", { class: "row", style: { justifyContent: "center", margin: "14px 0" } }, h("span", { class: "tag ok" }, "+" + r.xp + " XP"), r.level_up && h("span", { class: "tag prem" }, levelEmoji(r.new_level) + " " + t("earn.level_up", { n: r.new_level }))),
        r.retention > 0 && h("div", { class: "callout warn", style: { textAlign: "left", marginBottom: "14px" } }, icon("clock", 18), h("div", null, t("earn.locked_note", { n: r.retention }))),
        h("button", { class: "btn", onclick: () => { s.close(); onDone && onDone(); } }, t("earn.next"))));
      countUp(amt, r.reward, { fmtFn: (v) => "+" + fmt(v) });
    }

    draw();
  });
}
