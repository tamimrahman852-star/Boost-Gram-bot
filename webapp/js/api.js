// API client. Real backend contract is documented in webapp/API.md.
// When the app is opened outside Telegram (or with ?mock=1) a full in-browser mock is used so the
// whole UI can be demoed and designed without a backend.
import { initData, inTelegram } from "./tg.js";
import * as mock from "./mock.js";

export const demo = !inTelegram || new URLSearchParams(location.search).has("mock");
const BASE = (window.BG_API_BASE || "/api").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(code, message, status) { super(message || code); this.code = code; this.status = status; }
}

async function req(method, path, body) {
  const headers = { Authorization: "tma " + initData };
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res;
  try { res = await fetch(BASE + path, { method, headers, body: payload }); }
  catch (_) { throw new ApiError("network", "Network error", 0); }
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok) throw new ApiError((data && (data.code || data.detail?.code)) || "error", (data && (data.message || data.detail?.message || (typeof data.detail === "string" ? data.detail : ""))) || "Request failed", res.status);
  return data;
}
const qs = (o) => { const p = new URLSearchParams(); Object.entries(o || {}).forEach(([k, v]) => v != null && v !== "" && p.set(k, v)); const s = p.toString(); return s ? "?" + s : ""; };

const real = {
  bootstrap: (startParam) => req("GET", "/bootstrap" + qs({ start: startParam })),
  tasks: (type, cursor) => req("GET", "/tasks" + qs({ type, cursor })),
  startTask: (id) => req("POST", `/tasks/${id}/start`),
  verifyTask: (id, token) => req("POST", `/tasks/${id}/verify`, { token }),
  submitProof: (id, blob) => { const f = new FormData(); f.append("file", blob, "proof.jpg"); return req("POST", `/tasks/${id}/proof`, f); },
  campaigns: () => req("GET", "/campaigns"),
  resolveTarget: (type, link) => req("POST", "/campaigns/resolve", { type, link }),
  createCampaign: (data) => req("POST", "/campaigns", data),
  campaignAction: (id, action) => req("POST", `/campaigns/${id}/${action}`),
  reviews: () => req("GET", "/proofs/review"),
  reviewAction: (id, action) => req("POST", `/proofs/${id}/${action}`),
  history: (cursor, kind) => req("GET", "/wallet/history" + qs({ cursor, kind })),
  requests: () => req("GET", "/wallet/requests"),
  deposit: ({ method, amount_bdt, trx_id, blob }) => { const f = new FormData(); f.append("method", method); f.append("amount_bdt", amount_bdt); f.append("trx_id", trx_id); f.append("screenshot", blob, "pay.jpg"); return req("POST", "/deposits", f); },
  withdraw: (data) => req("POST", "/withdrawals", data),
  redeemVoucher: (code) => req("POST", "/vouchers/redeem", { code }),
  createCheck: (data) => req("POST", "/checks", data),
  redeemCheck: (code, password) => req("POST", "/checks/redeem", { code, password }),
  referrals: () => req("GET", "/referrals"),
  saveSettings: (data) => req("POST", "/settings", data),
};

export const api = demo ? mock.api : real;
