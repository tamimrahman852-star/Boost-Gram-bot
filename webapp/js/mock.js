// In-browser mock backend (demo mode). State persists in localStorage so flows feel real.
import { tgUser } from "./tg.js";

const KEY = "bg:mock:v1";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const uid = () => Math.random().toString(36).slice(2, 10);
const now = () => new Date().toISOString();
const ago = (m) => new Date(Date.now() - m * 60000).toISOString();
const params = new URLSearchParams(location.search);

class MockError extends Error { constructor(code, message) { super(message); this.code = code; } }

const RULES = {
  xp_per_level: 1500, withdraw_min_level: 30, withdraw_min: 250000, withdraw_max: 10000000,
  max_daily_tasks: 100, view_dwell: 6, proof_review_hours: 48, retention_days: 7,
  slots_min: 1, slots_max: 10000, max_active_campaigns: 10, commission: 0.15, premium_multiplier: 2,
};
const PRICING = {
  channel_sub: { min: 850, suggested: 1000, max: 5000 }, group_join: { min: 1250, suggested: 1500, max: 6000 },
  bot_start: { min: 2500, suggested: 3000, max: 10000 }, post_view: { min: 120, suggested: 200, max: 500 },
  reaction: { min: 500, suggested: 600, max: 800 }, boost_7day: { min: 25000, suggested: 30000, max: 100000 },
};
const LANGS = { en: "English", bn: "বাংলা", hi: "हिन्दी", ar: "العربية", ru: "Русский", es: "Español", tr: "Türkçe", id: "Indonesia", fa: "فارسی", fr: "Français", de: "Deutsch", pt: "Português", uk: "Українська", zh: "中文", ja: "日本語", ko: "한국어", vi: "Tiếng Việt", th: "ไทย", uz: "Oʻzbek", kk: "Қазақша", my: "မြန်မာ" };

function seed() {
  const level = Number(params.get("level")) || 14;
  const name = (tgUser && tgUser.first_name) || "Alex";
  const titles = [
    ["channel_sub", "Daily Crypto Pulse", "dailycryptopulse", 1000, 1800, 420, false],
    ["channel_sub", "FitLife Club", "fitlifeclub", 1200, 900, 130, false],
    ["group_join", "Dev Talks Community", "devtalkschat", 1500, 600, 410, false],
    ["post_view", "World Football Hub", "wfootballhub", 200, 5000, 3120, false],
    ["post_view", "Tech Weekly Digest", "techweekly", 220, 3000, 1100, false],
    ["bot_start", "QuickNote AI Bot", "quicknote_bot", 3000, 400, 98, false],
    ["reaction", "Meme Factory", "memefactory", 600, 1000, 220, false],
    ["boost_7day", "Gaming Zone Official", "gamingzoneofficial", 30000, 200, 64, true],
    ["channel_sub", "Premium Investors Circle", "premiuminvest", 2000, 300, 110, true],
    ["group_join", "Language Exchange", "langexchange", 1800, 700, 300, false],
    ["channel_sub", "Street Food Diaries", "streetfooddiaries", 950, 1200, 880, false],
    ["reaction", "Daily Motivation", "dailymotive", 700, 800, 410, false],
  ];
  const tasks = titles.map((t, i) => ({
    id: "t" + (i + 1), type: t[0], title: t[1], username: t[2], link: "https://t.me/" + t[2], reward: t[3],
    slots_total: t[4], slots_left: t[4] - t[5], premium: t[6], retention_days: ["channel_sub", "group_join", "boost_7day"].includes(t[0]) ? 7 : 0, state: "new",
  }));
  const hist = [
    ["task_reward", 1000, "Daily Crypto Pulse", 12], ["task_reward", 200, "World Football Hub", 26], ["referral_reward", 5000, "Referral bonus", 95],
    ["task_reward", 1500, "Dev Talks Community", 180], ["campaign_payment", -57500, "Campaign · Studio Hub", 600], ["star_topup", 150000, "Top up", 1500],
    ["task_reward", 3000, "QuickNote AI Bot", 1700], ["referral_tier_bonus", 420, "Tier-1 commission", 2900], ["task_reward", 1200, "FitLife Club", 3100],
  ].map((x) => ({ id: uid(), type: x[0], amount: x[1], title: x[2], created_at: ago(x[3]) }));
  return {
    user: {
      id: (tgUser && tgUser.id) || 100200300, first_name: name, username: (tgUser && tgUser.username) || "alex", is_premium: !!(tgUser && tgUser.is_premium) || params.has("premium"),
      photo_url: (tgUser && tgUser.photo_url) || null,
      balance: 384200, xp: (level - 1) * 1500 + 640, level, total_earned: 1260400, total_withdrawn: 0, completed_tasks: 418, referral_earnings: 38240,
      referral_code: "a1b2c3d4", referral_tier: 2, language: params.get("lang") || "en", notifications: true, locked: 21500, is_publisher: true, earned_balance: 9000000,
    },
    daily_used: 37, tasks, campaigns: [
      { id: "c1", type: "channel_sub", title: "Studio Hub", username: "studiohub", reward: 1000, slots_total: 250, completed: 187, status: "active", premium: false, auto_renew: 3, spent: 187000, created_at: ago(4300), pending_proofs: 0 },
      { id: "c2", type: "bot_start", title: "My Helper Bot", username: "myhelper_bot", reward: 3000, slots_total: 100, completed: 41, status: "active", premium: false, auto_renew: 0, spent: 123000, created_at: ago(1500), pending_proofs: 3 },
      { id: "c3", type: "post_view", title: "Studio Hub · Post", username: "studiohub", reward: 200, slots_total: 2000, completed: 2000, status: "completed", premium: false, auto_renew: 0, spent: 400000, created_at: ago(14000), pending_proofs: 0 },
    ],
    history: hist, requests: [{ id: uid(), kind: "deposit", amount: 150000, status: "approved", method: "bkash", created_at: ago(1500) }],
    referrals: [{ name: "Maria S.", joined: ago(420), earned: 5000 }, { name: "Jonas K.", joined: ago(3000), earned: 5420 }, { name: "Priya R.", joined: ago(9000), earned: 13400 }, { name: "Chen W.", joined: ago(20000), earned: 7020 }],
    ref_count: [14, 6, 3],
  };
}
let S = (() => { try { const s = JSON.parse(localStorage.getItem(KEY)); if (s && !params.has("reset") && !params.has("level")) return s; } catch (_) {} return seed(); })();
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (_) {} };
const publicUser = () => ({ ...S.user, current_level_xp: (S.user.level - 1) * 1500, next_level_xp: S.user.level * 1500, withdrawable: Math.max(0, S.user.balance - S.user.locked) });
const grant = (amount, title, type = "task_reward") => {
  const u = S.user; u.balance += amount; u.total_earned += amount; u.completed_tasks += 1;
  const before = u.level; u.xp += Math.max(1, Math.round(amount / 1000 * 3)); u.level = Math.floor(u.xp / 1500) + 1;
  S.history.unshift({ id: uid(), type, amount, title, created_at: now() }); S.daily_used += 1; save();
  return { level_up: u.level > before, new_level: u.level, xp: Math.max(1, Math.round(amount / 1000 * 3)) };
};
const needAmount = (v) => { if (!(v > 0)) throw new MockError("invalid", "Invalid amount"); };

export const api = {
  async bootstrap() {
    await sleep(500);
    return {
      user: publicUser(),
      config: {
        bot_username: "BoostGramPromotionBot", rules: RULES, pricing: PRICING, langs: LANGS,
        pay: { bkash: "01700-000000", nagad: "01800-000000", rocket: "01900-0000000" },
        deposit: { rate: 3000, min: 50, max: 20000 },
        stars: { enabled: true, rate: 5000, packages: [50, 100, 250, 750, 1499, 2499], min: 1, max: 10000 },
        ads: { commission: 0.2, min_members: 100, min_budget: 250000, lifetimes: [1, 3, 6, 12, 24, 48], default_lifetime: 6, external_links: false, view_sync: true, models: ["cpm", "cpc"], cpm: { min: 100000, default: 300000, max: 5000000 }, cpc: { min: 10000, default: 40000, max: 1000000 }, max_slots: 12, slot_gap: 30, video_mb: 10, image_mb: 8, max_strikes: 3 },
        legal: { terms: "terms.html", privacy: "privacy.html" },
        withdraw: { methods: ["usdt_trc20", "usdt_erc20", "bank", "mobile", "ton"] },
    portal: true,
    ton: { deposit: true, withdraw: true, min_deposit_usd: 1, wd_min_usd: 20, wd_max_usd: 1000, gram_per_usd: 366000, wd_min_gram: 7320000 },
        referral: { base: 5000, tiers: { 1: 0.08, 2: 0.05, 3: 0.02 } },
      },
      daily: { used: S.daily_used, max: RULES.max_daily_tasks },
      badges: { reviews: S.campaigns.reduce((a, c) => a + (c.pending_proofs || 0), 0) },
    };
  },
  async tasks(type) {
    await sleep(380);
    const items = S.tasks.filter((t) => t.state !== "done" && (!type || type === "all" || t.type === type) && (!t.premium || S.user.is_premium || true));
    return { items, daily: { used: S.daily_used, max: RULES.max_daily_tasks }, next: null };
  },
  async startTask(id) {
    await sleep(300);
    const t = S.tasks.find((x) => x.id === id); if (!t) throw new MockError("not_found", "Task not found");
    if (S.daily_used >= RULES.max_daily_tasks) throw new MockError("daily_limit", "Daily task limit reached");
    if (t.premium && !S.user.is_premium) throw new MockError("premium_only", "This task is for Telegram Premium users only");
    const kind = t.type === "post_view" ? "view" : ["bot_start", "reaction"].includes(t.type) ? "proof" : "join";
    return { kind, url: t.link, dwell: RULES.view_dwell, token: uid() };
  },
  async verifyTask(id) {
    await sleep(900);
    const t = S.tasks.find((x) => x.id === id); if (!t) throw new MockError("not_found", "Task not found");
    if (t.type === "channel_sub" || t.type === "group_join" || t.type === "boost_7day") {
      if (Math.random() < 0.18) throw new MockError("not_member", "We couldn't find you in the chat yet. Join first, then verify again.");
    }
    t.state = "done"; t.slots_left = Math.max(0, t.slots_left - 1);
    const r = grant(t.reward, t.title);
    return { ok: true, reward: t.reward, ...r, balance: S.user.balance, retention: t.retention_days || 0, user: publicUser(), daily: { used: S.daily_used, max: RULES.max_daily_tasks } };
  },
  async submitProof(id) {
    await sleep(900);
    const t = S.tasks.find((x) => x.id === id); if (t) t.state = "pending"; save();
    return { status: "pending", review_hours: RULES.proof_review_hours };
  },
  async campaigns() { await sleep(300); return { items: S.campaigns }; },
  async resolveTarget(type, link) {
    await sleep(700);
    const m = String(link).trim().match(/^(?:https?:\/\/)?(?:t\.me\/)?@?([A-Za-z][A-Za-z0-9_]{3,31})(?:\/(\d+))?$/);
    if (!m) throw new MockError("bad_link", "That doesn't look like a valid @username or t.me link");
    const username = m[1];
    if (type === "bot_start" && !/bot$/i.test(username)) throw new MockError("not_bot", "A bot username must end with “bot”");
    if (type === "post_view" && !m[2]) throw new MockError("no_post", "Paste the link of a specific post, e.g. t.me/channel/123");
    const title = username.replace(/_bot$/i, "").replace(/[_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    return { ok: true, title, username, members: type === "bot_start" ? null : 12480, bot_admin: !["bot_start", "reaction", "post_view"].includes(type) || !/test/i.test(username), kind: type === "bot_start" ? "bot" : "chat" };
  },
  async createCampaign({ type, link, title, username, premium, reward, slots, auto_renew }) {
    await sleep(900);
    const budget = reward * slots, fee = Math.ceil(budget * RULES.commission), total = budget + fee;
    if (S.campaigns.filter((c) => c.status === "active").length >= RULES.max_active_campaigns) throw new MockError("too_many", "Active campaign limit reached");
    if (total > S.user.balance) throw new MockError("insufficient_balance", "Not enough balance");
    S.user.balance -= total;
    S.history.unshift({ id: uid(), type: "campaign_payment", amount: -total, title: "Campaign · " + title, created_at: now() });
    const c = { id: "c" + uid(), type, title, username, reward, slots_total: slots, completed: 0, status: "active", premium, auto_renew: auto_renew ? 3 : 0, spent: 0, created_at: now(), pending_proofs: 0 };
    S.campaigns.unshift(c); save();
    return { campaign: c, balance: S.user.balance, charged: total };
  },
  async campaignAction(id, action) {
    await sleep(500);
    const c = S.campaigns.find((x) => x.id === id); if (!c) throw new MockError("not_found", "Not found");
    if (action === "pause") c.status = "paused";
    else if (action === "resume") c.status = "active";
    else if (action === "cancel") {
      const left = (c.slots_total - c.completed) * c.reward; c.status = "cancelled"; S.user.balance += left;
      S.history.unshift({ id: uid(), type: "campaign_refund", amount: left, title: "Refund · " + c.title, created_at: now() });
      save(); return { status: c.status, refunded: left, balance: S.user.balance };
    }
    save(); return { status: c.status };
  },
  async reviews() {
    await sleep(350);
    const svg = (a, b, t) => "data:image/svg+xml;utf8," + encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' width='540' height='960'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='${a}'/><stop offset='1' stop-color='${b}'/></linearGradient></defs><rect width='540' height='960' fill='url(#g)'/><rect x='30' y='60' width='480' height='70' rx='18' fill='rgba(255,255,255,.18)'/><rect x='30' y='160' width='330' height='90' rx='20' fill='rgba(255,255,255,.14)'/><rect x='170' y='270' width='340' height='70' rx='20' fill='rgba(255,255,255,.22)'/><text x='270' y='600' font-size='42' text-anchor='middle' fill='rgba(255,255,255,.7)' font-family='sans-serif'>${t}</text></svg>`);
    const out = [];
    S.campaigns.forEach((c) => { for (let i = 0; i < (c.pending_proofs || 0); i++) out.push({ id: c.id + ":" + i, campaign_id: c.id, campaign_title: c.title, type: c.type, reward: c.reward, worker: "Worker #" + (4000 + ((i * 97 + c.title.length * 13) % 900)), image: svg("#4338ca", "#0891b2", "Proof screenshot"), created_at: ago(40 + i * 130), expires_at: new Date(Date.now() + (48 - (i + 1) * 3) * 3600000).toISOString() }); });
    return { items: out };
  },
  async reviewAction(id, action) {
    await sleep(400);
    const [cid] = id.split(":"); const c = S.campaigns.find((x) => x.id === cid);
    if (c && c.pending_proofs > 0) { c.pending_proofs -= 1; if (action === "approve") { c.completed += 1; c.spent += c.reward; } }
    save(); return { ok: true };
  },
  async history(cursor, kind) {
    await sleep(300);
    let items = S.history.slice();
    if (kind === "in") items = items.filter((x) => x.amount > 0); else if (kind === "out") items = items.filter((x) => x.amount < 0);
    return { items, next: null };
  },
  async requests() { await sleep(200); return { items: S.requests }; },
  async deposit({ method, amount_bdt, trx_id }) {
    await sleep(1000);
    if (!/^[A-Za-z0-9]{6,16}$/.test(trx_id)) throw new MockError("bad_trx", "TrxID looks invalid");
    if (S.requests.filter((r) => r.kind === "deposit" && r.status === "pending").length >= 2) throw new MockError("too_many", "You already have 2 open deposit requests");
    S.requests.unshift({ id: uid(), kind: "deposit", amount: amount_bdt * 3000, status: "pending", method, created_at: now() }); save();
    return { status: "pending", gram: amount_bdt * 3000 };
  },
  async withdraw({ amount, method, details }) {
    await sleep(1000);
    if (S.user.level < RULES.withdraw_min_level) throw new MockError("level_locked", "Reach level " + RULES.withdraw_min_level + " to withdraw");
    if (amount < RULES.withdraw_min || amount > RULES.withdraw_max) throw new MockError("range", "Amount out of range");
    if (amount > S.user.balance - S.user.locked) throw new MockError("insufficient_balance", "Not enough withdrawable balance");
    if (!details) throw new MockError("invalid", "Payment details missing");
    S.user.balance -= amount; S.requests.unshift({ id: uid(), kind: "withdrawal", amount, status: "pending", method, created_at: now() });
    S.history.unshift({ id: uid(), type: "withdrawal", amount: -amount, title: "Withdrawal request", created_at: now() }); save();
    return { status: "pending", balance: S.user.balance };
  },
  async redeemVoucher(code) {
    await sleep(700);
    if (String(code).trim().toUpperCase() !== "WELCOME") throw new MockError("bad_code", "Invalid or expired code (try WELCOME in demo)");
    S.user.balance += 25000; S.history.unshift({ id: uid(), type: "admin_adjustment", amount: 25000, title: "Promo code", created_at: now() }); save();
    return { amount: 25000, balance: S.user.balance };
  },
  async createCheck({ amount, uses, password }) {
    await sleep(800);
    const total = amount * uses; if (total > S.user.balance) throw new MockError("insufficient_balance", "Not enough balance");
    S.user.balance -= total; S.history.unshift({ id: uid(), type: "check_create", amount: -total, title: "Check created", created_at: now() }); save();
    const code = uid().toUpperCase();
    return { code, link: "https://t.me/BoostGramPromotionBot?start=check_" + code, balance: S.user.balance, locked: !!password };
  },
  async redeemCheck(code) {
    await sleep(700); if (!code || code.length < 6) throw new MockError("bad_code", "Check not found");
    S.user.balance += 10000; S.history.unshift({ id: uid(), type: "check_redeem", amount: 10000, title: "Check redeemed", created_at: now() }); save();
    return { amount: 10000, balance: S.user.balance };
  },
  async referrals() {
    await sleep(350);
    return { code: S.user.referral_code, link: "https://t.me/BoostGramPromotionBot?start=ref_" + S.user.referral_code, count: S.ref_count.reduce((a, b) => a + b, 0), earnings: S.user.referral_earnings, tier: S.user.referral_tier, tiers: [1, 2, 3].map((l, i) => ({ level: l, percent: [8, 5, 2][i], count: S.ref_count[i], earned: [24300, 10440, 3500][i] })), recent: S.referrals };
  },
  async saveSettings(data) {
    if (data.language) S.user.language = data.language;
    if (typeof data.notifications === "boolean") S.user.notifications = data.notifications;
    save(); return { ok: true };
  },
};

// ---------- ads marketplace mock (in-memory) ----------
const dayKey = (i) => new Date(Date.now() - i * 86400000).toISOString().slice(5, 10);
const mkSeries = (n, mv, mc) => Array.from({ length: n }, (_, i) => ({ date: dayKey(n - 1 - i), views: Math.round(mv * (0.5 + Math.abs(Math.sin(i * 1.7)))), clicks: Math.round(mc * (0.4 + Math.abs(Math.cos(i * 1.3)))), earned: 0, spent: 0 }));
const mkStats = (v, c, st, paid, earned) => ({ posts: 12, completed: 10, live: 2, views: v, impressions: v, clicks: c, bot_starts: st, ctr: +(c / v * 100).toFixed(2), spent: paid, fee: Math.round(paid * 0.2), earned, pending: 0, cpc: c ? Math.round(paid / c) : null, cpm: v ? Math.round(paid / v * 1000) : null, rpm: earned ? Math.round(earned / v * 1000) : null, epc: earned && c ? Math.round(earned / c) : null });
const ADS = {
  channels: [{ id: "ch1", title: "Daily Crypto Pulse", username: "dailycryptopulse", members: 18400, status: "active", pause_reason: null, tz_offset: 360, slots: ["09:00", "14:00", "21:00"], lifetime: 6, auto_delete: true, strikes: 0, total_posts: 12, earned: 4000000, stats: mkStats(212000, 3900, 410, 5760000, 4000000), created_at: ago(9000) }],
  campaigns: [{ id: "cp1", title: "Join my crypto channel", text: "Daily signals and news.", has_image: false, media_type: null, pricing: "cpm", bid: 300000, button_text: "Join now", button_url: "https://t.me/mychan", status: "active", reject_reason: null, budget: 1000000, spent: 360000, refunded: 0, remaining: 640000, posts_per_day: 6, min_members: 0, start_at: ago(3000), end_at: new Date(Date.now() + 4 * 86400000).toISOString(), posts: 2, stats: mkStats(9400, 188, 21, 360000, 300000), created_at: ago(3100) }],
};
const adsSeries = () => mkSeries(14, 9000, 190);
Object.assign(api, {
  async depositStars(stars) { await sleep(500); if (!(stars >= 1)) throw new MockError("range", "Invalid amount"); return { url: "https://t.me/$demo", gram: stars * 5000, stars }; },
  async adChannels() { await sleep(350); return { items: ADS.channels.filter((c) => c.status !== "removed"), candidates: [{ chat_id: -1001, title: "My New Channel", username: "mynewchannel", members: 2300 }], rules: { commission: 0.2, min_members: 100, lifetimes: [1, 3, 6, 12, 24, 48], max_channels: 10, min_budget: 250000, max_strikes: 3, external_links: false, view_sync: true, models: ["cpm", "cpc"], add_link: "https://t.me/BoostGramPromotionBot?startchannel=true&admin=post_messages+edit_messages+delete_messages" } }; },
  async adAddChannel(d) { await sleep(700); if (ADS.channels.some((c) => c.username === String(d.username || "mynewchannel").replace("@", ""))) throw new MockError("duplicate", "You already added this channel."); const ch = { ...ADS.channels[0], id: uid(), title: "My New Channel", username: String(d.username || "mynewchannel").replace("@", ""), members: 2300, slots: ["10:00", "16:00", "21:00"], earned: 0, total_posts: 0, stats: mkStats(1, 0, 0, 0, 0) }; ch.stats = { ...ch.stats, posts: 0, completed: 0, live: 0, views: 0, clicks: 0, ctr: 0, cpc: null, cpm: null, rpm: null, epc: null }; ADS.channels.unshift(ch); return { channel: ch }; },
  async adChannelStats(id) { await sleep(400); const c = ADS.channels.find((x) => x.id === id); if (!c) throw new MockError("not_found", "Not found"); return { channel: c, series: adsSeries(), next_at: new Date(Date.now() + 3600e3).toISOString(), eligible_campaigns: 2, diag: [], recent: [{ id: "p1", status: "completed", posted_at: ago(1400), views: 14200, clicks: 260, bot_starts: 31, earn: 4260, slot: "2026-11-19 14:00", campaign_title: "Join my crypto channel" }, { id: "p2", status: "posted", posted_at: ago(120), views: 3100, clicks: 54, bot_starts: 6, earn: 744, slot: "2026-11-20 21:00", campaign_title: "Fitness app launch" }] }; },
  async adChannelUpdate(id, d) { await sleep(400); const c = ADS.channels.find((x) => x.id === id); if (!d.slots || !d.slots.length) throw new MockError("bad_slots", "Pick at least one time"); Object.assign(c, { slots: [...d.slots].sort(), lifetime: d.lifetime, auto_delete: d.auto_delete, tz_offset: d.tz_offset }); return { channel: c }; },
  async adChannelAction(id, a) { await sleep(300); const c = ADS.channels.find((x) => x.id === id); c.status = a === "pause" ? "paused" : a === "resume" ? "active" : "removed"; return { status: c.status }; },
  async adEstimate({ pricing, bid, budget }) { await sleep(200); const pub = Math.round(budget * 0.8); return { channels: 42, slots_per_day: 118, audience: 640000, commission: 0.2, publisher_share: pub, platform_fee: budget - pub, views: pricing === "cpm" && bid ? Math.floor(budget / bid * 1000) : null, clicks: pricing === "cpc" && bid ? Math.floor(budget / bid) : null }; },
  async adCampaigns() { await sleep(350); const totals = mkStats(9400, 188, 21, 360000, 300000); return { items: ADS.campaigns, totals, rules: { commission: 0.2 } }; },
  async portalLink() { await sleep(300); return { url: location.origin + "/portal", expires_in: 180 }; },
  async tonInfo() { await sleep(300); return { enabled: true, wallet: "UQDemoWalletAddressForTests0000000000000000000000", memo: "BG" + S.user.id, min_usd: 1, gram_per_usd: 366000, ton_usd: 3.2, gram_per_ton: 1171200, history: [] }; },
  async tonCheck() { await sleep(600); return { credited: 0 }; },
  async adPreview() { await sleep(500); return { ok: true }; },
  async adPreviewCampaign() { await sleep(500); return { ok: true }; },
  async adCreateCampaign(o) { await sleep(900); if (!/^https:\/\/t\.me\//i.test(o.button_url)) throw new MockError("bad_link", "Only t.me links are accepted for ads."); if (o.budget > S.user.balance) throw new MockError("insufficient_balance", "Not enough balance"); S.user.balance -= o.budget; S.history.unshift({ id: uid(), type: "campaign_payment", amount: -o.budget, title: "Ad campaign", created_at: now() }); save(); const c = { ...ADS.campaigns[0], id: uid(), title: o.title, text: o.text, button_text: o.button_text, button_url: o.button_url, pricing: o.pricing, bid: o.bid, media_type: o.blob ? (o.blob.type || "").startsWith("video") ? "video" : "photo" : null, status: "pending_review", budget: o.budget, spent: 0, remaining: o.budget, stats: { ...mkStats(1, 0, 0, 0, 0), posts: 0, views: 0, clicks: 0, ctr: 0, cpc: null, cpm: null } }; ADS.campaigns.unshift(c); return { campaign: c, balance: S.user.balance }; },
  async adCampaign(id) { await sleep(400); const c = ADS.campaigns.find((x) => x.id === id); if (!c) throw new MockError("not_found", "Not found"); return { campaign: c, series: adsSeries(), placements: [{ id: "x1", status: "completed", posted_at: ago(1500), views: 5200, clicks: 110, cost: 180000, channel_title: "Daily Crypto Pulse", channel_username: "dailycryptopulse" }], preview: c.text }; },
  async adCampaignAction(id, a, body) { await sleep(400); const c = ADS.campaigns.find((x) => x.id === id); const out = {}; if (a === "pause") c.status = "paused"; else if (a === "resume") c.status = "active"; else if (a === "stop") { S.user.balance += c.remaining; out.refunded = c.remaining; c.remaining = 0; c.status = "stopped"; } else if (a === "fund") { if (body.amount > S.user.balance) throw new MockError("insufficient_balance", "Not enough balance"); S.user.balance -= body.amount; c.budget += body.amount; c.remaining += body.amount; } save(); return { status: c.status, remaining: c.remaining, balance: S.user.balance, ...out }; },
});
