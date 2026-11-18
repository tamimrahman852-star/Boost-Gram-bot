// UI translations. English + Bangla are complete; other bot languages fall back to English
// (the language choice is still saved so the bot itself speaks it). Add a language by adding a dict below.
import { store } from "./tg.js";

const en = {
  "tab.home": "Home", "tab.earn": "Earn", "tab.promote": "Promote", "tab.wallet": "Wallet", "tab.me": "Profile",
  "common.back": "Back", "common.continue": "Continue", "common.copied": "Copied", "common.copy": "Copy", "common.done": "Done", "common.optional": "Optional",
  "common.retry": "Try again", "common.see_all": "See all", "common.share": "Share",
  "time.now": "just now", "time.m": "{n}m ago", "time.h": "{n}h ago", "time.d": "{n}d ago", "time.d_short": "d", "time.today": "Today", "time.yesterday": "Yesterday",

  "home.night": "Late night grind", "home.morning": "Good morning", "home.afternoon": "Good afternoon", "home.evening": "Good evening",
  "home.balance": "Total balance", "home.level": "Level {n}", "home.deposit": "Deposit", "home.daily": "Daily task limit",
  "home.earned": "Total earned", "home.tasks_done": "Tasks done", "home.ref_earned": "Referral income", "home.locked": "In retention",
  "home.hot": "Top paying tasks", "home.invite_title": "Invite friends, earn forever", "home.invite_sub": "{n} GRAM per friend + up to 8% of their income", "home.activity": "Recent activity",

  "earn.all": "All", "earn.today": "today", "earn.empty": "No tasks right now", "earn.empty_sub": "New tasks appear all the time — check back soon.",
  "earn.premium": "Premium", "earn.in_review": "In review", "earn.in_review_toast": "Your proof is waiting for the advertiser's review",
  "earn.step_open": "Open the chat and join (or boost) it", "earn.step_verify": "Come back here and tap Verify", "earn.step_stay": "Stay for {n} days — leaving early removes the reward and adds a penalty",
  "earn.step_view": "Tap “View post” — the post is shown to you in the bot chat", "earn.step_wait": "Wait {s} seconds — your reward is paid automatically",
  "earn.step_open_do": "Open it and complete the action (start the bot / react)", "earn.step_shot": "Take a screenshot as proof", "earn.step_review": "The advertiser checks it within {h}h — otherwise it is auto-approved",
  "earn.retention_warn": "The reward stays locked for {n} days while we re-check that you are still a member.", "earn.boost_note": "Boost tasks need Telegram Premium and the bot must be able to read boosts.",
  "earn.open_in_tg": "Open in Telegram", "earn.open_again": "Open again", "earn.verify": "Verify", "earn.view_post": "View post", "earn.submit_proof": "Submit proof",
  "earn.pick_shot": "Choose screenshot", "earn.shot_hint": "One screenshot can only be used once", "earn.view_wait": "Viewing the post…", "earn.view_wait_sub": "Keep this open to get paid",
  "earn.proof_sent": "Proof submitted", "earn.proof_sent_sub": "The advertiser has up to {h} hours to review it. You'll be paid once approved.",
  "earn.reward_in": "Reward received!", "earn.level_up": "Level {n}!", "earn.locked_note": "This reward is locked for {n} days (retention check) and then becomes withdrawable.", "earn.next": "Next task",

  "promo.create": "Create", "promo.mine": "My ads", "promo.reviews": "Reviews", "promo.step": "Step {n} of {m}",
  "promo.s1_title": "What do you want to grow?", "promo.s1_sub": "Pick the kind of task people will complete for you.", "promo.from": "from {n} GRAM",
  "promo.hint_channel_sub": "Real subscribers, re-checked after 7 days", "promo.hint_group_join": "Active members for your group", "promo.hint_boost_7day": "Telegram Premium boosts for 7 days",
  "promo.hint_post_view": "Real views on a specific post", "promo.hint_bot_start": "Users who start your bot (proof checked)", "promo.hint_reaction": "Reactions on a post (proof checked)",
  "promo.s2_title": "Where should we send people?", "promo.s2_chat": "Paste your channel or group @username or t.me link.", "promo.s2_bot": "Paste your bot's @username.", "promo.s2_post": "Paste the link of the exact post (t.me/channel/123).",
  "promo.link": "Link or @username", "promo.found": "Found", "promo.members": "members", "promo.need_admin": "Add {bot} as an administrator (with invite permission) or members cannot be verified.",
  "promo.admin_tip": "Make {bot} an admin in your channel/group so we can verify every member for you.",
  "promo.s3_title": "Audience & reward", "promo.s3_sub": "Higher rewards fill faster.", "promo.aud_all": "Everyone", "promo.aud_prem": "Premium only", "promo.prem_note": "Premium users are a higher-quality audience — rewards are ×{n}.",
  "promo.reward_per": "Reward per user", "promo.p_min": "Min", "promo.p_suggested": "Best", "promo.p_max": "Max", "promo.auto_renew": "Auto-renew", "promo.auto_renew_sub": "Republish up to 3 times when finished",
  "promo.s4_title": "How many people?", "promo.s4_sub": "Review the cost and launch.", "promo.user": "user", "promo.slots": "Slots", "promo.people": "people",
  "promo.sum_budget": "Task rewards", "promo.sum_fee": "Platform fee ({n}%)", "promo.sum_total": "Total", "promo.sum_after": "Balance after",
  "promo.no_funds": "You need {n} more GRAM", "promo.launch": "Launch campaign", "promo.live": "Campaign is live!", "promo.live_sub": "{n} people can now complete your task.", "promo.view_mine": "View my ads",
  "promo.none": "No campaigns yet", "promo.none_sub": "Create your first one in under a minute.", "promo.st_active": "Active", "promo.st_paused": "Paused", "promo.st_completed": "Completed", "promo.st_cancelled": "Cancelled",
  "promo.to_review": "to review", "promo.audience": "Audience", "promo.spent": "Spent", "promo.created": "Created", "promo.review_n": "Review {n} proofs", "promo.pause": "Pause", "promo.resume": "Resume", "promo.cancel": "Cancel",
  "promo.cancel_q": "Cancel this campaign? {n} GRAM for unused slots will be refunded.", "promo.refunded": "Refunded {n} GRAM",
  "promo.review_info": "Approve or reject screenshots from workers. Pending proofs are auto-approved after {h}h.", "promo.no_reviews": "Nothing to review", "promo.no_reviews_sub": "New proofs will show up here.",
  "promo.auto_in": "auto in {n}h", "promo.approve": "Approve", "promo.reject": "Reject", "promo.approved": "Approved & paid", "promo.rejected": "Rejected",

  "wallet.deposit": "Deposit", "wallet.withdraw": "Withdraw", "wallet.withdrawal": "Withdrawal", "wallet.gifts": "Gifts", "wallet.history": "History",
  "wallet.available": "Withdrawable", "wallet.locked": "In retention", "wallet.requests": "Requests", "wallet.st_pending": "Pending", "wallet.st_approved": "Approved", "wallet.st_rejected": "Rejected",
  "wallet.dep_off": "Deposits are temporarily unavailable.", "wallet.method": "Payment method", "wallet.amount_bdt": "Amount", "wallet.min_dep": "Minimum {n} BDT",
  "wallet.send_to": "Send money to", "wallet.send_hint": "Use “Send Money” on {m} and send", "wallet.trx": "Transaction ID (TrxID)", "wallet.trx_once": "Each TrxID works once",
  "wallet.shot": "Payment screenshot", "wallet.shot_sub": "Upload the confirmation screen", "wallet.submit_dep": "Submit deposit",
  "wallet.dep_note": "An admin checks every deposit manually. Coins are credited only after approval.", "wallet.dep_sent": "Deposit submitted", "wallet.dep_sent_sub": "{n} GRAM will be credited after admin approval.",
  "wallet.wd_locked": "Withdrawals unlock at level {n}", "wallet.wd_locked_sub": "Reach level {n} to cash out your earnings.", "wallet.wd_levels_left": "{n} levels · {xp} XP", "wallet.wd_tip": "Complete tasks and invite friends to level up faster.",
  "wallet.amount": "Amount", "wallet.wd_range": "Min {a} · Max {b} GRAM", "wallet.wd_method": "Payout method", "wallet.wd_details": "Payout details", "wallet.wd_details_ph": "Wallet address / account number",
  "wallet.wd_note": "Rewards still inside the {n}-day retention window cannot be withdrawn. One pending request at a time.", "wallet.request_wd": "Request withdrawal", "wallet.wd_sent": "Request sent", "wallet.wd_sent_sub": "An admin will review it shortly.",
  "wallet.promo": "Promo code", "wallet.promo_sub": "Have a code? Redeem free GRAM.", "wallet.redeem": "Redeem", "wallet.checks": "GRAM checks", "wallet.checks_sub": "Send coins to anyone with a link.", "wallet.redeem_check": "Redeem check", "wallet.create_check": "Create a check",
  "wallet.per_use": "GRAM per activation", "wallet.uses": "Activations", "wallet.check_pw": "Password", "wallet.check_ready": "Your check is ready", "wallet.check_share": "I sent you a GRAM check 🎁",
  "wallet.income": "Income", "wallet.expense": "Spending", "wallet.no_history": "No transactions yet",

  "invite.title": "Invite friends", "invite.hero": "Earn {n} GRAM per friend", "invite.hero_sub": "Plus a lifetime share of what they earn — across 3 levels.", "invite.friends": "friends",
  "invite.your_link": "Your personal link", "invite.share_text": "Earn Telegram coins with me on Boost Gram 🚀", "invite.tiers": "Commission levels", "invite.tier": "Level {n} referrals",
  "invite.tier_sub_1": "Friends you invite directly", "invite.tier_sub_2": "Friends of your friends", "invite.tier_sub_3": "Third level network", "invite.how": "How it works",
  "invite.h1": "Share your link with friends", "invite.h2": "They join and complete their first tasks — you get {n} GRAM", "invite.h3": "You keep earning a % of everything they earn", "invite.recent": "Recent friends", "invite.none": "No friends yet — share your link!",

  "me.to_next": "{n} XP to level {l}", "me.withdrawn": "Withdrawn", "me.badges": "Achievements", "me.settings": "Settings", "me.language": "Language", "me.language_sub": "Changes the bot language too.",
  "me.lang_bot_only": "{l} saved for the bot — the app stays in English for now", "me.theme": "Appearance", "me.theme_auto": "Auto", "me.theme_dark": "Dark", "me.theme_light": "Light",
  "me.notifications": "Notifications", "me.notifications_sub": "Task, referral & payout updates", "me.haptics": "Haptic feedback", "me.haptics_sub": "Subtle vibrations on taps", "me.invite_sub": "Earn from every friend", "me.open_bot": "Open the bot",
  "badge.first": "First task", "badge.grinder": "100 tasks", "badge.veteran": "1,000 tasks", "badge.lvl15": "Level 15", "badge.lvl30": "Level 30", "badge.million": "1M earned", "badge.recruiter": "Recruiter", "badge.premium": "Premium", "badge.withdrawn": "Cashed out",

  "type.channel_sub": "Channel subscribe", "type.group_join": "Group join", "type.boost_7day": "Channel boost (7 days)", "type.post_view": "Post view", "type.bot_start": "Start a bot", "type.reaction": "Post reaction",
  "type.s_channel_sub": "Channels", "type.s_group_join": "Groups", "type.s_boost_7day": "Boosts", "type.s_post_view": "Views", "type.s_bot_start": "Bots", "type.s_reaction": "Reactions",
  "tx.task_reward": "Task reward", "tx.referral_reward": "Referral", "tx.referral_tier_bonus": "Commission", "tx.star_topup": "Deposit", "tx.withdrawal": "Withdrawal", "tx.campaign_payment": "Campaign", "tx.campaign_refund": "Refund",
  "tx.check_redeem": "Check", "tx.check_create": "Check", "tx.penalty_revoke": "Penalty", "tx.admin_adjustment": "Bonus",

  "err.generic": "Something went wrong", "err.offline": "You're offline", "err.network": "Network error — check your connection", "err.bad_image": "That image couldn't be read", "err.invalid": "Please check the values",
  "err.premium_only": "This task is for Telegram Premium users", "err.daily_limit": "Daily task limit reached — come back tomorrow", "err.not_member": "We couldn't find you in the chat yet. Join first, then verify again.",
  "err.bad_link": "That doesn't look like a valid @username or t.me link", "err.not_bot": "A bot username must end with “bot”", "err.no_post": "Paste the link of a specific post, e.g. t.me/channel/123",
  "err.insufficient_balance": "Not enough balance", "err.too_many": "You've reached the limit for open items", "err.bad_trx": "That TrxID looks invalid", "err.level_locked": "Your level is too low to withdraw yet",
  "err.range": "Amount is out of range", "err.bad_code": "Invalid or expired code", "err.not_found": "Not found", "err.blocked": "Your account is blocked", "err.cooldown": "Too fast — wait a moment",
};

const bn = {
  "tab.home": "হোম", "tab.earn": "আয়", "tab.promote": "প্রচার", "tab.wallet": "ওয়ালেট", "tab.me": "প্রোফাইল",
  "common.back": "পেছনে", "common.continue": "এগিয়ে যান", "common.copied": "কপি হয়েছে", "common.copy": "কপি", "common.done": "ঠিক আছে", "common.optional": "ঐচ্ছিক",
  "common.retry": "আবার চেষ্টা করুন", "common.see_all": "সব দেখুন", "common.share": "শেয়ার",
  "time.now": "এইমাত্র", "time.m": "{n} মি. আগে", "time.h": "{n} ঘণ্টা আগে", "time.d": "{n} দিন আগে", "time.d_short": "দিন", "time.today": "আজ", "time.yesterday": "গতকাল",

  "home.night": "শুভ রাত্রি", "home.morning": "শুভ সকাল", "home.afternoon": "শুভ অপরাহ্ন", "home.evening": "শুভ সন্ধ্যা",
  "home.balance": "মোট ব্যালেন্স", "home.level": "লেভেল {n}", "home.deposit": "ডিপোজিট", "home.daily": "দৈনিক টাস্ক লিমিট",
  "home.earned": "মোট আয়", "home.tasks_done": "সম্পন্ন টাস্ক", "home.ref_earned": "রেফারেল আয়", "home.locked": "রিটেনশনে",
  "home.hot": "সর্বোচ্চ পেমেন্টের টাস্ক", "home.invite_title": "বন্ধুদের ইনভাইট করে আজীবন আয় করুন", "home.invite_sub": "প্রতি বন্ধুতে {n} GRAM + তাদের আয়ের ৮% পর্যন্ত", "home.activity": "সাম্প্রতিক কার্যকলাপ",

  "earn.all": "সব", "earn.today": "আজ", "earn.empty": "এখন কোনো টাস্ক নেই", "earn.empty_sub": "নতুন টাস্ক নিয়মিত আসে — একটু পরে দেখুন।",
  "earn.premium": "প্রিমিয়াম", "earn.in_review": "রিভিউতে", "earn.in_review_toast": "আপনার প্রুফ বিজ্ঞাপনদাতার রিভিউয়ের অপেক্ষায় আছে",
  "earn.step_open": "চ্যাটটি খুলে জয়েন (বা বুস্ট) করুন", "earn.step_verify": "এখানে ফিরে এসে ভেরিফাই চাপুন", "earn.step_stay": "{n} দিন থাকুন — আগে ছাড়লে রিওয়ার্ড কাটা যাবে ও পেনাল্টি হবে",
  "earn.step_view": "“পোস্ট দেখুন” চাপুন — পোস্টটি বট চ্যাটে দেখানো হবে", "earn.step_wait": "{s} সেকেন্ড অপেক্ষা করুন — রিওয়ার্ড স্বয়ংক্রিয়ভাবে যোগ হবে",
  "earn.step_open_do": "খুলে কাজটি করুন (বট স্টার্ট / রিঅ্যাক্ট)", "earn.step_shot": "প্রমাণ হিসেবে স্ক্রিনশট নিন", "earn.step_review": "বিজ্ঞাপনদাতা {h} ঘণ্টার মধ্যে দেখবেন — না দেখলে অটো-অ্যাপ্রুভ হবে",
  "earn.retention_warn": "আপনি এখনও মেম্বার আছেন কিনা যাচাই করতে রিওয়ার্ড {n} দিন লক থাকবে।", "earn.boost_note": "বুস্ট টাস্কে টেলিগ্রাম প্রিমিয়াম লাগে এবং বটকে বুস্ট দেখার অনুমতি থাকতে হবে।",
  "earn.open_in_tg": "টেলিগ্রামে খুলুন", "earn.open_again": "আবার খুলুন", "earn.verify": "ভেরিফাই", "earn.view_post": "পোস্ট দেখুন", "earn.submit_proof": "প্রুফ জমা দিন",
  "earn.pick_shot": "স্ক্রিনশট বাছুন", "earn.shot_hint": "একটি স্ক্রিনশট একবারই ব্যবহার করা যায়", "earn.view_wait": "পোস্ট দেখা হচ্ছে…", "earn.view_wait_sub": "পেমেন্ট পেতে এটি খোলা রাখুন",
  "earn.proof_sent": "প্রুফ জমা হয়েছে", "earn.proof_sent_sub": "বিজ্ঞাপনদাতা {h} ঘণ্টা পর্যন্ত সময় পাবেন। অ্যাপ্রুভ হলে পেমেন্ট পাবেন।",
  "earn.reward_in": "রিওয়ার্ড পেয়েছেন!", "earn.level_up": "লেভেল {n}!", "earn.locked_note": "এই রিওয়ার্ড {n} দিন লক থাকবে (রিটেনশন চেক), তারপর উইথড্র করা যাবে।", "earn.next": "পরের টাস্ক",

  "promo.create": "তৈরি", "promo.mine": "আমার বিজ্ঞাপন", "promo.reviews": "রিভিউ", "promo.step": "ধাপ {n}/{m}",
  "promo.s1_title": "কী বাড়াতে চান?", "promo.s1_sub": "মানুষ আপনার জন্য কোন টাস্ক করবে তা বাছুন।", "promo.from": "{n} GRAM থেকে",
  "promo.hint_channel_sub": "আসল সাবস্ক্রাইবার, ৭ দিন পর আবার যাচাই", "promo.hint_group_join": "আপনার গ্রুপের জন্য সক্রিয় মেম্বার", "promo.hint_boost_7day": "৭ দিনের টেলিগ্রাম প্রিমিয়াম বুস্ট",
  "promo.hint_post_view": "নির্দিষ্ট পোস্টে আসল ভিউ", "promo.hint_bot_start": "আপনার বট স্টার্ট করবে (প্রুফ যাচাই)", "promo.hint_reaction": "পোস্টে রিঅ্যাকশন (প্রুফ যাচাই)",
  "promo.s2_title": "কোথায় পাঠাবো?", "promo.s2_chat": "চ্যানেল/গ্রুপের @ইউজারনেম বা t.me লিংক দিন।", "promo.s2_bot": "আপনার বটের @ইউজারনেম দিন।", "promo.s2_post": "নির্দিষ্ট পোস্টের লিংক দিন (t.me/channel/123)।",
  "promo.link": "লিংক বা @ইউজারনেম", "promo.found": "পাওয়া গেছে", "promo.members": "মেম্বার", "promo.need_admin": "{bot} কে অ্যাডমিন (ইনভাইট পারমিশনসহ) করুন, নাহলে মেম্বার যাচাই হবে না।",
  "promo.admin_tip": "প্রতিটি মেম্বার যাচাই করতে {bot} কে আপনার চ্যানেল/গ্রুপে অ্যাডমিন করুন।",
  "promo.s3_title": "অডিয়েন্স ও রিওয়ার্ড", "promo.s3_sub": "বেশি রিওয়ার্ডে দ্রুত সম্পন্ন হয়।", "promo.aud_all": "সবাই", "promo.aud_prem": "শুধু প্রিমিয়াম", "promo.prem_note": "প্রিমিয়াম ইউজার উন্নত মানের অডিয়েন্স — রিওয়ার্ড ×{n}।",
  "promo.reward_per": "প্রতি ইউজারে রিওয়ার্ড", "promo.p_min": "সর্বনিম্ন", "promo.p_suggested": "সেরা", "promo.p_max": "সর্বোচ্চ", "promo.auto_renew": "অটো-রিনিউ", "promo.auto_renew_sub": "শেষ হলে ৩ বার পর্যন্ত আবার চালু হবে",
  "promo.s4_title": "কতজন চাই?", "promo.s4_sub": "খরচ দেখে চালু করুন।", "promo.user": "জন", "promo.slots": "স্লট", "promo.people": "জন",
  "promo.sum_budget": "টাস্ক রিওয়ার্ড", "promo.sum_fee": "প্ল্যাটফর্ম ফি ({n}%)", "promo.sum_total": "মোট", "promo.sum_after": "পরে ব্যালেন্স",
  "promo.no_funds": "আরও {n} GRAM দরকার", "promo.launch": "ক্যাম্পেইন চালু করুন", "promo.live": "ক্যাম্পেইন চালু হয়েছে!", "promo.live_sub": "এখন {n} জন আপনার টাস্ক করতে পারবে।", "promo.view_mine": "আমার বিজ্ঞাপন দেখুন",
  "promo.none": "এখনও কোনো ক্যাম্পেইন নেই", "promo.none_sub": "এক মিনিটেই প্রথমটি তৈরি করুন।", "promo.st_active": "চালু", "promo.st_paused": "বিরতি", "promo.st_completed": "সম্পন্ন", "promo.st_cancelled": "বাতিল",
  "promo.to_review": "রিভিউ বাকি", "promo.audience": "অডিয়েন্স", "promo.spent": "খরচ", "promo.created": "তৈরি", "promo.review_n": "{n}টি প্রুফ রিভিউ করুন", "promo.pause": "বিরতি", "promo.resume": "চালু", "promo.cancel": "বাতিল",
  "promo.cancel_q": "ক্যাম্পেইন বাতিল করবেন? অব্যবহৃত স্লটের {n} GRAM ফেরত পাবেন।", "promo.refunded": "{n} GRAM ফেরত",
  "promo.review_info": "ওয়ার্কারদের স্ক্রিনশট অ্যাপ্রুভ বা রিজেক্ট করুন। {h} ঘণ্টা পর অটো-অ্যাপ্রুভ হবে।", "promo.no_reviews": "রিভিউ করার কিছু নেই", "promo.no_reviews_sub": "নতুন প্রুফ এখানে আসবে।",
  "promo.auto_in": "{n} ঘণ্টায় অটো", "promo.approve": "অ্যাপ্রুভ", "promo.reject": "রিজেক্ট", "promo.approved": "অ্যাপ্রুভ ও পেমেন্ট হয়েছে", "promo.rejected": "রিজেক্ট হয়েছে",

  "wallet.deposit": "ডিপোজিট", "wallet.withdraw": "উইথড্র", "wallet.withdrawal": "উইথড্র", "wallet.gifts": "উপহার", "wallet.history": "ইতিহাস",
  "wallet.available": "উইথড্রযোগ্য", "wallet.locked": "রিটেনশনে", "wallet.requests": "অনুরোধ", "wallet.st_pending": "অপেক্ষমাণ", "wallet.st_approved": "অনুমোদিত", "wallet.st_rejected": "বাতিল",
  "wallet.dep_off": "ডিপোজিট সাময়িকভাবে বন্ধ।", "wallet.method": "পেমেন্ট মাধ্যম", "wallet.amount_bdt": "পরিমাণ", "wallet.min_dep": "সর্বনিম্ন {n} টাকা",
  "wallet.send_to": "এই নম্বরে পাঠান", "wallet.send_hint": "{m}-এ “সেন্ড মানি” করে পাঠান", "wallet.trx": "ট্রানজেকশন আইডি (TrxID)", "wallet.trx_once": "প্রতিটি TrxID একবারই চলে",
  "wallet.shot": "পেমেন্টের স্ক্রিনশট", "wallet.shot_sub": "কনফার্মেশন স্ক্রিন আপলোড করুন", "wallet.submit_dep": "ডিপোজিট জমা দিন",
  "wallet.dep_note": "অ্যাডমিন প্রতিটি ডিপোজিট নিজে যাচাই করেন। অনুমোদনের পরেই কয়েন যোগ হয়।", "wallet.dep_sent": "ডিপোজিট জমা হয়েছে", "wallet.dep_sent_sub": "অ্যাডমিন অনুমোদন করলে {n} GRAM যোগ হবে।",
  "wallet.wd_locked": "লেভেল {n}-এ উইথড্র চালু হবে", "wallet.wd_locked_sub": "আয় ক্যাশ আউট করতে লেভেল {n} -এ পৌঁছান।", "wallet.wd_levels_left": "{n} লেভেল · {xp} XP", "wallet.wd_tip": "টাস্ক করে ও বন্ধুদের ইনভাইট করে দ্রুত লেভেল আপ করুন।",
  "wallet.amount": "পরিমাণ", "wallet.wd_range": "সর্বনিম্ন {a} · সর্বোচ্চ {b} GRAM", "wallet.wd_method": "পেআউট মাধ্যম", "wallet.wd_details": "পেআউট তথ্য", "wallet.wd_details_ph": "ওয়ালেট ঠিকানা / অ্যাকাউন্ট নম্বর",
  "wallet.wd_note": "{n} দিনের রিটেনশনের মধ্যে থাকা রিওয়ার্ড উইথড্র করা যায় না। একসাথে একটিই পেন্ডিং অনুরোধ।", "wallet.request_wd": "উইথড্র অনুরোধ", "wallet.wd_sent": "অনুরোধ পাঠানো হয়েছে", "wallet.wd_sent_sub": "অ্যাডমিন শীঘ্রই দেখবেন।",
  "wallet.promo": "প্রোমো কোড", "wallet.promo_sub": "কোড আছে? ফ্রি GRAM নিন।", "wallet.redeem": "রিডিম", "wallet.checks": "GRAM চেক", "wallet.checks_sub": "লিংকের মাধ্যমে যে কাউকে কয়েন পাঠান।", "wallet.redeem_check": "চেক রিডিম", "wallet.create_check": "চেক তৈরি করুন",
  "wallet.per_use": "প্রতি ব্যবহারে GRAM", "wallet.uses": "ব্যবহারের সংখ্যা", "wallet.check_pw": "পাসওয়ার্ড", "wallet.check_ready": "আপনার চেক তৈরি", "wallet.check_share": "আমি তোমাকে একটি GRAM চেক পাঠালাম 🎁",
  "wallet.income": "আয়", "wallet.expense": "খরচ", "wallet.no_history": "এখনও কোনো লেনদেন নেই",

  "invite.title": "বন্ধুদের ইনভাইট", "invite.hero": "প্রতি বন্ধুতে {n} GRAM", "invite.hero_sub": "সাথে তাদের আয়ের আজীবন অংশ — ৩ স্তর পর্যন্ত।", "invite.friends": "বন্ধু",
  "invite.your_link": "আপনার ব্যক্তিগত লিংক", "invite.share_text": "আমার সাথে Boost Gram-এ টেলিগ্রাম কয়েন আয় করুন 🚀", "invite.tiers": "কমিশন লেভেল", "invite.tier": "লেভেল {n} রেফারেল",
  "invite.tier_sub_1": "আপনার সরাসরি ইনভাইট করা বন্ধু", "invite.tier_sub_2": "বন্ধুর বন্ধু", "invite.tier_sub_3": "তৃতীয় স্তরের নেটওয়ার্ক", "invite.how": "কীভাবে কাজ করে",
  "invite.h1": "বন্ধুদের সাথে লিংক শেয়ার করুন", "invite.h2": "তারা জয়েন করে প্রথম টাস্ক করলে আপনি {n} GRAM পাবেন", "invite.h3": "তাদের প্রতিটি আয়ের একটি অংশ আপনিও পাবেন", "invite.recent": "সাম্প্রতিক বন্ধু", "invite.none": "এখনও কোনো বন্ধু নেই — লিংক শেয়ার করুন!",

  "me.to_next": "লেভেল {l}-এর জন্য আরও {n} XP", "me.withdrawn": "উইথড্র", "me.badges": "অর্জন", "me.settings": "সেটিংস", "me.language": "ভাষা", "me.language_sub": "বটের ভাষাও বদলে যাবে।",
  "me.lang_bot_only": "{l} বটের জন্য সেভ হয়েছে — অ্যাপ আপাতত ইংরেজিতে থাকবে", "me.theme": "থিম", "me.theme_auto": "অটো", "me.theme_dark": "ডার্ক", "me.theme_light": "লাইট",
  "me.notifications": "নোটিফিকেশন", "me.notifications_sub": "টাস্ক, রেফারেল ও পেআউট আপডেট", "me.haptics": "হ্যাপটিক ফিডব্যাক", "me.haptics_sub": "ট্যাপে হালকা ভাইব্রেশন", "me.invite_sub": "প্রতিটি বন্ধু থেকে আয় করুন", "me.open_bot": "বট খুলুন",
  "badge.first": "প্রথম টাস্ক", "badge.grinder": "১০০ টাস্ক", "badge.veteran": "১,০০০ টাস্ক", "badge.lvl15": "লেভেল ১৫", "badge.lvl30": "লেভেল ৩০", "badge.million": "১M আয়", "badge.recruiter": "রিক্রুটার", "badge.premium": "প্রিমিয়াম", "badge.withdrawn": "ক্যাশ আউট",

  "type.channel_sub": "চ্যানেল সাবস্ক্রাইব", "type.group_join": "গ্রুপ জয়েন", "type.boost_7day": "চ্যানেল বুস্ট (৭ দিন)", "type.post_view": "পোস্ট ভিউ", "type.bot_start": "বট স্টার্ট", "type.reaction": "পোস্ট রিঅ্যাকশন",
  "type.s_channel_sub": "চ্যানেল", "type.s_group_join": "গ্রুপ", "type.s_boost_7day": "বুস্ট", "type.s_post_view": "ভিউ", "type.s_bot_start": "বট", "type.s_reaction": "রিঅ্যাকশন",
  "tx.task_reward": "টাস্ক রিওয়ার্ড", "tx.referral_reward": "রেফারেল", "tx.referral_tier_bonus": "কমিশন", "tx.star_topup": "ডিপোজিট", "tx.withdrawal": "উইথড্র", "tx.campaign_payment": "ক্যাম্পেইন", "tx.campaign_refund": "ফেরত",
  "tx.check_redeem": "চেক", "tx.check_create": "চেক", "tx.penalty_revoke": "পেনাল্টি", "tx.admin_adjustment": "বোনাস",

  "err.generic": "কিছু একটা ভুল হয়েছে", "err.offline": "আপনি অফলাইন", "err.network": "নেটওয়ার্ক সমস্যা — সংযোগ দেখুন", "err.bad_image": "ছবিটি পড়া যায়নি", "err.invalid": "মানগুলো ঠিক আছে কিনা দেখুন",
  "err.premium_only": "এই টাস্ক শুধু টেলিগ্রাম প্রিমিয়াম ইউজারদের জন্য", "err.daily_limit": "দৈনিক টাস্ক লিমিট শেষ — আগামীকাল আসুন", "err.not_member": "আপনাকে এখনও চ্যাটে পাওয়া যায়নি। আগে জয়েন করুন, তারপর ভেরিফাই করুন।",
  "err.bad_link": "এটি সঠিক @ইউজারনেম বা t.me লিংক মনে হচ্ছে না", "err.not_bot": "বটের ইউজারনেম “bot” দিয়ে শেষ হতে হবে", "err.no_post": "নির্দিষ্ট পোস্টের লিংক দিন, যেমন t.me/channel/123",
  "err.insufficient_balance": "ব্যালেন্স যথেষ্ট নয়", "err.too_many": "খোলা আইটেমের সীমা শেষ", "err.bad_trx": "TrxID সঠিক মনে হচ্ছে না", "err.level_locked": "উইথড্র করতে আপনার লেভেল এখনও কম",
  "err.range": "পরিমাণ সীমার বাইরে", "err.bad_code": "কোড ভুল বা মেয়াদোত্তীর্ণ", "err.not_found": "পাওয়া যায়নি", "err.blocked": "আপনার অ্যাকাউন্ট ব্লক করা", "err.cooldown": "খুব দ্রুত — একটু অপেক্ষা করুন",
};

const DICTS = { en, bn };
let lang = "en";
export const getLang = () => lang;
export function setLang(code, persist = true) {
  const ok = !!DICTS[code];
  if (ok) { lang = code; document.documentElement.lang = code; persist && store.set("lang", code); }
  return ok;
}
export function t(key, vars) {
  let s = (DICTS[lang] && DICTS[lang][key]) ?? en[key] ?? key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] != null ? vars[k] : "{" + k + "}"));
  return s;
}
export const _dicts = DICTS;
