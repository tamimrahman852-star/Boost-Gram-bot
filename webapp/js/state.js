// Shared app state + small helpers used by every view.
import { t } from "./i18n.js";
import { toast } from "./ui.js";

export const S = { user: null, config: null, daily: { used: 0, max: 100 }, badges: { reviews: 0 }, startParam: "" };
const subs = new Set();
export const subscribe = (f) => { subs.add(f); return () => subs.delete(f); };
export const emit = () => subs.forEach((f) => f());
export function patchUser(u) { if (u) { Object.assign(S.user, u); emit(); } }
export function setDaily(d) { if (d) { S.daily = d; emit(); } }

export const TYPE_ORDER = ["channel_sub", "group_join", "boost_7day", "post_view", "bot_start", "reaction"];
export const TYPES = {
  channel_sub: { icon: "radio", cls: "g1" },
  group_join: { icon: "users", cls: "g5" },
  boost_7day: { icon: "rocket", cls: "g4" },
  post_view: { icon: "eye", cls: "g2" },
  bot_start: { icon: "bot", cls: "g3" },
  reaction: { icon: "heart", cls: "g3" },
};
export const isVerifiable = (type) => ["channel_sub", "group_join", "boost_7day"].includes(type);

export const TX = {
  task_reward: { icon: "bolt", cls: "g2" }, referral_reward: { icon: "users", cls: "g4" }, referral_tier_bonus: { icon: "trophy", cls: "g4" },
  star_topup: { icon: "down", cls: "g2" }, withdrawal: { icon: "up", cls: "g3" }, campaign_payment: { icon: "megaphone", cls: "g3" },
  campaign_refund: { icon: "refresh", cls: "g2" }, check_redeem: { icon: "ticket", cls: "g2" }, check_create: { icon: "ticket", cls: "g3" },
  penalty_revoke: { icon: "warn", cls: "g3" }, admin_adjustment: { icon: "gift", cls: "g1" },
};

export const levelProgress = (u) => {
  const span = u.next_level_xp - u.current_level_xp || 1;
  return Math.max(0, Math.min(1, (u.xp - u.current_level_xp) / span));
};
export const levelEmoji = (l) => (l >= 50 ? "👑" : l >= 30 ? "💎" : l >= 15 ? "🔥" : l >= 5 ? "⚡" : "🌱");

/** Show a translated error for an ApiError / MockError. */
export function fail(e) {
  const key = "err." + (e && e.code);
  const msg = e && e.code && t(key) !== key ? t(key) : (e && e.message) || t("err.generic");
  toast(msg, "err");
}
