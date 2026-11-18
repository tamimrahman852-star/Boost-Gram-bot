"""Multi-language engine.

MANUAL SYSTEM (default, no API needed): Bangla and English are in the code; every other language is a
hand-written file `locales/<lang>.tsv` -> one line per text:  <line-number><TAB><translation>
(line numbers refer to `locales/lines.json`). Edit those files with any text editor to improve a translation.
`python tools/check_locales.py` validates them (placeholders such as {amount} and tags such as <b> must survive).

OPTIONAL automatic translation (off by default, needs an API): set I18N_AUTO_TRANSLATE=1 plus a provider key.
Whatever is missing in a manual file falls back to English; nothing ever crashes.

* Placeholders such as {amount:,.0f} and HTML tags such as <b> are protected during translation and
  verified afterwards; anything that doesn't survive falls back to a safer chunked translation or to English.
* `locales/overrides/<lang>.json` ({"English text": "your translation"}) is an extra place for hand-polished texts; it always wins.

Provider (environment):
  GEMINI_API_KEY [+ GEMINI_MODEL] -> Google AI Studio / Gemini (best quality, understands context)
  GOOGLE_TRANSLATE_API_KEY  -> official Google Cloud Translation (recommended for production)
  LIBRETRANSLATE_URL [+ LIBRETRANSLATE_API_KEY] -> LibreTranslate server
  (neither)                 -> the free public Google endpoint (works without a key, best-effort)
"""
import asyncio
import hashlib
import json
import os
import re
import string
from typing import Dict, Iterable, List, Optional, Set

import aiohttp

from config import logger

BASE_LANGS = ("en", "bn")
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(_BASE_DIR, "locales", "catalog.json")
OVERRIDE_DIR = os.path.join(_BASE_DIR, "locales", "overrides")

_cache: Dict[str, Dict[str, str]] = {}      # lang -> {key: translated template}
_runtime_missing: Set[str] = set()          # English templates seen at runtime that have no translation yet
_tasks: Dict[str, asyncio.Task] = {}
_catalog_cache: Optional[List[str]] = None

# Test/diagnostic helper: set I18N_RECORD=/path/file.json to dump every text that was looked up.
_record: Optional[Set[str]] = set() if os.getenv("I18N_RECORD") else None
if _record is not None:
    import atexit

    @atexit.register
    def _dump_record():
        with open(os.environ["I18N_RECORD"], "a", encoding="utf-8") as fh:
            fh.write(json.dumps(sorted(_record), ensure_ascii=False) + "\n")

last_error: str = ""        # most recent provider problem (shown by the admin /i18n command; never contains keys)


def _scrub(msg: str) -> str:
    """Remove API keys from any text that may be logged or shown to the admin."""
    for var in ("GEMINI_API_KEY", "GOOGLE_TRANSLATE_API_KEY", "LIBRETRANSLATE_API_KEY"):
        secret = os.getenv(var, "").strip()
        if secret:
            msg = msg.replace(secret, "***")
    return msg


def _set_error(msg: str) -> None:
    global last_error
    last_error = _scrub(msg)[:300]
    logger.warning(f"i18n provider: {last_error}")


_PROTECT_RE = re.compile(r"\{\{|\}\}|\{[^{}]*\}|</?[a-zA-Z][^>]*>")
_TOKEN_RE = re.compile(r"\{(\d+)\}")


def key_of(template: str) -> str:
    return hashlib.sha1(template.encode("utf-8")).hexdigest()[:16]


def supported(lang: str) -> bool:
    from localization import L10N
    return lang in L10N.LANGS


def lookup(lang: str, template: str) -> Optional[str]:
    return _cache.get(lang, {}).get(key_of(template))


def note_missing(template: str) -> None:
    if template and len(_runtime_missing) < 5000:
        _runtime_missing.add(template)


def L(lang: str, bn: str, en: str, _tpl: Optional[str] = None, _kw: Optional[dict] = None) -> str:
    """Pick the text for a language. bn/en are the hand-written versions (already formatted);
    other languages use the cached machine translation of the English *template*."""
    if _record is not None:
        _record.add(_tpl if _tpl is not None else en)
    if lang == "bn":
        return bn
    if lang == "en" or not lang or not supported(lang):
        return en
    tpl = _tpl if _tpl is not None else en
    tr = lookup(lang, tpl)
    if tr is None:
        note_missing(tpl)
        return en
    if _tpl is not None:
        try:
            return tr.format(**(_kw or {}))
        except Exception:
            return en
    return tr


def variants(en_text: str, bn_text: Optional[str] = None) -> Set[str]:
    """Every known spelling of a button text (used to match reply-keyboard taps in any language)."""
    out = {en_text}
    if bn_text:
        out.add(bn_text)
    k = key_of(en_text)
    for tr in _cache.values():
        v = tr.get(k)
        if v:
            out.add(v)
    return out


# ------------------------------------------------------------------ catalog
def catalog() -> List[str]:
    global _catalog_cache
    if _catalog_cache is None:
        try:
            with open(CATALOG_PATH, encoding="utf-8") as f:
                _catalog_cache = json.load(f)
        except Exception:
            _catalog_cache = []
    seen, out = set(), []
    for t in list(_catalog_cache) + sorted(_runtime_missing):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ------------------------------------------------------------------ providers
GOOGLE_CODES = {"zh": "zh-CN", "my": "my", "uk": "uk", "fa": "fa", "uz": "uz", "kk": "kk"}
LIBRE_CODES = {"zh": "zh", "my": "my", "uz": "uz"}


class Provider:
    name = "base"
    batch = 1

    async def translate(self, lang: str, texts: List[str]) -> List[Optional[str]]:
        raise NotImplementedError

    async def ping(self) -> List[tuple]:
        try:
            out = await asyncio.wait_for(self.translate("hi", ["Hello"]), 25)
            return [(self.name, "✅ works" if out and out[0] else f"❌ no answer ({last_error or 'unknown'})")]
        except Exception as e:
            return [(self.name, f"❌ {type(e).__name__}")]


LANG_NAMES = {
    "hi": "Hindi", "ru": "Russian", "ar": "Arabic", "es": "Spanish", "de": "German", "fr": "French",
    "pt": "Brazilian Portuguese", "id": "Indonesian", "tr": "Turkish", "fa": "Persian (Farsi)",
    "uz": "Uzbek (Latin script)", "kk": "Kazakh (Cyrillic script)", "uk": "Ukrainian", "zh": "Simplified Chinese",
    "vi": "Vietnamese", "th": "Thai", "ja": "Japanese", "ko": "Korean", "my": "Burmese",
}


class Gemini(Provider):
    """Google AI Studio (Gemini API). Sends the strings as a JSON array and expects a JSON array back."""
    name = "gemini"
    batch = 40
    URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    MAX_CHARS = 5000
    RETRY_BASE = 3            # seconds; a rate-limited model is retried once, then the next model is used
    TIMEOUT = 40              # seconds per request
    COOLDOWN = 60             # a model that answered 429/5xx is skipped for this long
    PING_TIMEOUT = 20
    PROMPT = (
        "You are a professional UI translator for a Telegram bot (earn/promote platform). "
        "Translate every English string in the JSON array into {lang}.\n"
        "Rules:\n"
        "- Tokens like {{0}} {{1}} {{2}} are placeholders: keep each one exactly once, unchanged.\n"
        "- Keep emojis, numbers, symbols (· → • № #) and names such as GRAM, Telegram, Premium, bKash, Nagad, Rocket, Stars unchanged.\n"
        "- Wording: short, natural and friendly, suitable for buttons and chat messages.\n"
        "- Do not add explanations. Return ONLY a JSON array of strings with exactly the same length and order as the input.\n\n"
        "INPUT:\n"
    )

    def __init__(self, key: str, model: str = ""):
        self.key = key
        self.models = [m for m in dict.fromkeys([model, "gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"]) if m]
        self.min_interval = float(os.getenv("GEMINI_MIN_INTERVAL", "4.5"))   # stay below the free-tier request limit
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._cool: Dict[str, float] = {}

    @staticmethod
    def _message(text: str) -> str:
        try:
            return _scrub(str(json.loads(text)["error"]["message"]))[:170]
        except Exception:
            return _scrub(text)[:170].replace("\n", " ")

    async def _post(self, session, model: str, body: dict, locked: bool = True):
        url = self.URL_TMPL.format(model=model)
        headers = {"x-goog-api-key": self.key}
        if not locked:
            async with session.post(url, json=body, headers=headers) as r:
                return r.status, await r.text()
        async with self._lock:                       # one request at a time, spaced out
            wait = self.min_interval - (asyncio.get_event_loop().time() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with session.post(url, json=body, headers=headers) as r:
                    return r.status, await r.text()
            finally:
                self._last = asyncio.get_event_loop().time()

    async def _try_model(self, session, model: str, body: dict, n: int):
        """Returns: list (success) | "next" (model unusable right now) | "split" (bad answer) | None (fatal: key/permission)."""
        for attempt in range(2):
            try:
                status, text = await self._post(session, model, body)
            except Exception as e:
                _set_error(f"Gemini {model}: {type(e).__name__} (network/timeout)")
                if attempt == 0:
                    await asyncio.sleep(self.RETRY_BASE)
                    continue
                self._cool[model] = asyncio.get_event_loop().time() + self.COOLDOWN
                return "next"
            if status == 200:
                try:
                    parts = json.loads(text)["candidates"][0]["content"]["parts"]
                    out = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
                    if isinstance(out, list) and len(out) == n and all(isinstance(x, str) for x in out):
                        return out
                except Exception:
                    pass
                _set_error(f"Gemini {model}: unexpected answer format")
                return "split"
            if status in (429, 500, 502, 503, 504):
                _set_error(f"Gemini {model} HTTP {status}: {self._message(text)}")
                if attempt == 0:
                    await asyncio.sleep(self.RETRY_BASE)
                    continue
                self._cool[model] = asyncio.get_event_loop().time() + self.COOLDOWN
                return "next"
            if status == 404:
                _set_error(f"Gemini model {model} not available (HTTP 404)")
                return "next"
            _set_error(f"Gemini {model} HTTP {status}: {self._message(text)}")      # 400/401/403: bad key / API not enabled
            return None
        return "next"

    def _order(self) -> List[str]:
        now = asyncio.get_event_loop().time()
        ready = [m for m in self.models if self._cool.get(m, 0) <= now]
        return ready or list(self.models)

    async def _call(self, session, lang: str, texts: List[str], depth: int = 0) -> Optional[List[str]]:
        body = {
            "contents": [{"parts": [{"text": self.PROMPT.format(lang=LANG_NAMES.get(lang, lang)) + json.dumps(texts, ensure_ascii=False)}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
        want_split = False
        for model in self._order():
            res = await self._try_model(session, model, body, len(texts))
            if isinstance(res, list):
                if self.models[0] != model:                      # remember the model that works
                    self.models.remove(model)
                    self.models.insert(0, model)
                return res
            if res == "next":
                if self.models.count(model) and len(self.models) > 1 and self._cool.get(model, 0) == 0:
                    self.models.remove(model)                   # 404: never ask a missing model again
                continue
            if res is None:
                return None
            want_split = True
            break
        if want_split and len(texts) > 1 and depth < 3:
            mid = len(texts) // 2
            a = await self._call(session, lang, texts[:mid], depth + 1)
            b = await self._call(session, lang, texts[mid:], depth + 1)
            if a is not None and b is not None:
                return a + b
        return None

    async def translate(self, lang, texts):
        out: List[Optional[str]] = []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.TIMEOUT)) as session:
            i = 0
            while i < len(texts):
                chunk, size = [], 0
                while i < len(texts) and (not chunk or size + len(texts[i]) < self.MAX_CHARS):
                    chunk.append(texts[i])
                    size += len(texts[i])
                    i += 1
                res = await self._call(session, lang, chunk)
                out.extend(res if res is not None else [None] * len(chunk))
        return out

    async def ping(self) -> List[tuple]:
        """Fast diagnostic for the admin: one tiny request per model (no queue, no retries)."""
        body = {"contents": [{"parts": [{"text": "Reply with the JSON array [\"ok\"]"}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
        results = []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.PING_TIMEOUT)) as session:
            for model in self.models:
                try:
                    status, text = await self._post(session, model, body, locked=False)
                except Exception as e:
                    results.append((model, f"❌ {type(e).__name__} (no answer in time / network)"))
                    continue
                if status == 200:
                    results.append((model, "✅ works"))
                    break
                results.append((model, f"❌ HTTP {status}: {self._message(text)}"))
        return results


class GoogleFree(Provider):
    name = "google_free"
    batch = 8
    URL = "https://translate.googleapis.com/translate_a/single"

    async def _one(self, session, sem, lang, text):
        params = {"client": "gtx", "sl": "en", "tl": GOOGLE_CODES.get(lang, lang), "dt": "t", "q": text}
        for attempt in range(3):
            try:
                async with sem:
                    async with session.get(self.URL, params=params) as r:
                        if r.status == 200:
                            data = await r.json(content_type=None)
                            return "".join(seg[0] for seg in data[0] if seg and seg[0])
                        if r.status in (429, 503):
                            await asyncio.sleep(2 * (attempt + 1))
                            continue
                        _set_error(f"Google (free endpoint) HTTP {r.status}")
                        return None
            except Exception:
                await asyncio.sleep(1 + attempt)
        return None

    async def translate(self, lang, texts):
        sem = asyncio.Semaphore(4)
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            return await asyncio.gather(*[self._one(session, sem, lang, t) for t in texts])


class GoogleCloud(Provider):
    name = "google_cloud"
    batch = 100
    URL = "https://translation.googleapis.com/language/translate/v2"

    def __init__(self, key: str):
        self.key = key

    async def translate(self, lang, texts):
        url = f"{self.URL}?key={self.key}"
        body = {"q": texts, "source": "en", "target": GOOGLE_CODES.get(lang, lang), "format": "text"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.post(url, json=body) as r:
                    if r.status != 200:
                        _set_error(f"Google Cloud Translation HTTP {r.status}")
                        return [None] * len(texts)
                    data = await r.json()
            return [x.get("translatedText") for x in data["data"]["translations"]]
        except Exception as e:
            logger.warning(f"Google Cloud Translation failed: {e}")
            return [None] * len(texts)


class LibreTranslate(Provider):
    name = "libretranslate"
    batch = 50

    def __init__(self, url: str, key: str = ""):
        self.url, self.key = url.rstrip("/"), key

    async def translate(self, lang, texts):
        body = {"q": texts, "source": "en", "target": LIBRE_CODES.get(lang, lang), "format": "text"}
        if self.key:
            body["api_key"] = self.key
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                async with session.post(f"{self.url}/translate", json=body) as r:
                    if r.status != 200:
                        _set_error(f"LibreTranslate HTTP {r.status}")
                        return [None] * len(texts)
                    data = await r.json()
            res = data.get("translatedText")
            return res if isinstance(res, list) else [res]
        except Exception as e:
            logger.warning(f"LibreTranslate failed: {e}")
            return [None] * len(texts)


_provider: Optional[Provider] = None


def get_provider() -> Optional[Provider]:
    global _provider
    if _provider is None and not auto_enabled():
        return None
    if _provider is None:
        gem = os.getenv("GEMINI_API_KEY", "").strip()
        gk = os.getenv("GOOGLE_TRANSLATE_API_KEY", "").strip()
        lt = os.getenv("LIBRETRANSLATE_URL", "").strip()
        if gem:
            _provider = Gemini(gem, os.getenv("GEMINI_MODEL", "").strip())
        elif gk:
            _provider = GoogleCloud(gk)
        elif lt:
            _provider = LibreTranslate(lt, os.getenv("LIBRETRANSLATE_API_KEY", "").strip())
        else:
            _provider = GoogleFree()
        logger.info(f"Translation provider: {_provider.name}")
    return _provider


def set_provider(p: Provider) -> None:
    global _provider
    _provider = p


# ------------------------------------------------------------------ protected translation
def _protect(line: str):
    parts: List[str] = []

    def sub(m):
        parts.append(m.group(0))
        return "{%d}" % (len(parts) - 1)

    return _PROTECT_RE.sub(sub, line), parts


def _restore(text: str, parts: List[str]) -> Optional[str]:
    found = _TOKEN_RE.findall(text)
    if sorted(found) != sorted(str(i) for i in range(len(parts))):
        return None
    return _TOKEN_RE.sub(lambda m: parts[int(m.group(1))], text)


def _has_letters(s: str) -> bool:
    return any(ch.isalpha() for ch in s)


def needs_translation(line: str) -> bool:
    """False for lines made only of placeholders / tags / emoji / numbers (nothing to translate)."""
    protected, _ = _protect(line)
    return _has_letters(_TOKEN_RE.sub("", protected))


def _fields(template: str) -> Optional[List[str]]:
    try:
        return sorted(f[1] for f in string.Formatter().parse(template) if f[1] is not None)
    except ValueError:
        return None


def _tags(template: str) -> List[str]:
    return sorted(re.findall(r"</?[a-zA-Z]+", template))


async def translate_lines(lang: str, lines: List[str]) -> Dict[str, str]:
    """Translate unique single lines; returns {line: translation} for the lines that succeeded."""
    provider = get_provider()
    if provider is None:
        return {}
    todo = [ln for ln in dict.fromkeys(lines) if ln.strip()]
    result: Dict[str, str] = {}
    need: List[str] = []
    for ln in todo:
        protected, _ = _protect(ln)
        if _has_letters(_TOKEN_RE.sub("", protected)):
            need.append(ln)
        else:
            result[ln] = ln                                     # numbers / emoji / tags only
    # 1) whole line with protected tokens, in provider-sized batches
    failed: List[str] = []
    for i in range(0, len(need), provider.batch):
        chunk = need[i:i + provider.batch]
        prot = [_protect(c) for c in chunk]
        out = await provider.translate(lang, [p[0] for p in prot])
        for src, (_, parts), tr in zip(chunk, prot, out):
            restored = _restore(tr, parts) if tr else None
            if restored is not None:
                result[src] = restored
            else:
                failed.append(src)
    # 2) fallback: translate only the plain text between placeholders/tags
    for src in failed:
        pieces = _PROTECT_RE.split(src)
        spans = _PROTECT_RE.findall(src)
        texts = [p for p in pieces if _has_letters(p)]
        out = await provider.translate(lang, [t.strip() for t in texts]) if texts else []
        if len(out) != len(texts) or any(o is None for o in out):
            continue
        it = iter(out)
        rebuilt = []
        for idx, p in enumerate(pieces):
            if _has_letters(p):
                tr = next(it)
                lead = p[: len(p) - len(p.lstrip())]
                trail = p[len(p.rstrip()):]
                rebuilt.append(lead + tr + trail)
            else:
                rebuilt.append(p)
            if idx < len(spans):
                rebuilt.append(spans[idx])
        result[src] = "".join(rebuilt)
    return result


async def translate_templates(lang: str, templates: List[str]) -> Dict[str, str]:
    all_lines = [ln for t in templates for ln in t.split("\n")]
    tr_lines = await translate_lines(lang, all_lines)
    out: Dict[str, str] = {}
    for t in templates:
        parts = []
        ok = True
        for ln in t.split("\n"):
            if not ln.strip():
                parts.append(ln)
            elif ln in tr_lines:
                parts.append(tr_lines[ln])
            else:
                ok = False
                break
        if not ok:
            continue
        cand = "\n".join(parts)
        # the translation must keep exactly the same placeholders and tags, or it is discarded
        if _fields(cand) is None or _fields(cand) != _fields(t) or _tags(cand) != _tags(t):
            continue
        out[t] = cand
    return out


# ------------------------------------------------------------------ manual locale files
LOCALES_DIR = os.path.join(_BASE_DIR, "locales")
LINES_PATH = os.path.join(LOCALES_DIR, "lines.json")


def auto_enabled() -> bool:
    """Automatic (API) translation is opt-in; the manual files are the normal way."""
    return os.getenv("I18N_AUTO_TRANSLATE", "").strip() == "1"


def read_tsv(path: str) -> Dict[int, str]:
    out: Dict[int, str] = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.rstrip("\n").rstrip("\r")
            if not raw.strip() or raw.lstrip().startswith("#") or "\t" not in raw:
                continue
            num, text = raw.split("\t", 1)
            if num.strip().isdigit() and text.strip():
                out[int(num)] = text
    return out


def manual_languages() -> List[str]:
    try:
        return sorted(fn[:-4] for fn in os.listdir(LOCALES_DIR) if fn.endswith(".tsv"))
    except FileNotFoundError:
        return []


def load_manual() -> Dict[str, int]:
    """Load locales/<lang>.tsv into the in-memory cache. Returns {lang: translated templates}."""
    try:
        with open(LINES_PATH, encoding="utf-8") as fh:
            lines = json.load(fh)
    except Exception as e:
        logger.warning(f"i18n: cannot read lines.json ({e})")
        return {}
    templates = [t for t in (json.load(open(CATALOG_PATH, encoding="utf-8")) if os.path.exists(CATALOG_PATH) else [])]
    result: Dict[str, int] = {}
    for lang in manual_languages():
        if not supported(lang):
            continue
        try:
            tr_by_index = read_tsv(os.path.join(LOCALES_DIR, lang + ".tsv"))
        except Exception as e:
            logger.warning(f"i18n: cannot read {lang}.tsv ({e})")
            continue
        by_line = {lines[i]: tr for i, tr in tr_by_index.items() if 0 <= i < len(lines)}
        n = 0
        for tpl in templates:
            parts = []
            for ln in tpl.split("\n"):
                if not ln.strip() or not needs_translation(ln):
                    parts.append(ln)
                elif ln in by_line:
                    parts.append(by_line[ln])
                else:
                    parts = None
                    break
            if parts is None:
                continue
            cand = "\n".join(parts)
            if _fields(cand) is None or _fields(cand) != _fields(tpl) or _tags(cand) != _tags(tpl):
                continue                                   # damaged placeholders: ignore, English is used instead
            _cache.setdefault(lang, {})[key_of(tpl)] = cand
            n += 1
        result[lang] = n
    return result


def coverage() -> Dict[str, tuple]:
    """{lang: (templates translated, templates total)} for every supported non-base language."""
    from localization import L10N
    total = len(catalog())
    return {l: (len(_cache.get(l, {})), total) for l in L10N.LANGS if l not in BASE_LANGS}


# ------------------------------------------------------------------ persistence / warm-up
def _load_overrides() -> None:
    try:
        for fn in os.listdir(OVERRIDE_DIR):
            if fn.endswith(".json"):
                lang = fn[:-5]
                with open(os.path.join(OVERRIDE_DIR, fn), encoding="utf-8") as f:
                    for en, tr in json.load(f).items():
                        _cache.setdefault(lang, {})[key_of(en)] = tr
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"override load failed: {e}")


async def load_cache() -> None:
    """Fill the in-memory cache at startup. Priority (lowest -> highest):
    stored automatic translations (DB)  <  manual locales/<lang>.tsv  <  locales/overrides/*.json
    so hand-written texts always win over anything machine-made."""
    from sqlalchemy import select
    from database import AsyncSessionLocal, Translation
    try:
        async with AsyncSessionLocal() as s:
            for row in (await s.execute(select(Translation.lang, Translation.key, Translation.text))).all():
                _cache.setdefault(row[0], {})[row[1]] = row[2]
    except Exception as e:
        logger.warning(f"translation cache load failed: {e}")
    manual = load_manual()
    _load_overrides()
    logger.info(f"i18n ready - manual files: {manual}")


async def _store(lang: str, items: Dict[str, str]) -> None:
    from database import AsyncSessionLocal, Translation
    try:
        async with AsyncSessionLocal() as s:
            for en, tr in items.items():
                await s.merge(Translation(lang=lang, key=key_of(en), text=tr))
            await s.commit()
    except Exception as e:
        logger.warning(f"translation store failed: {e}")


def missing_for(lang: str) -> List[str]:
    return [t for t in catalog() if lookup(lang, t) is None]


async def warm_language(lang: str, chunk: int = 25) -> int:
    """Translate everything missing for `lang`. Returns how many templates were added."""
    if lang in BASE_LANGS or not supported(lang) or get_provider() is None:
        return 0
    added = 0
    empty = 0
    todo = missing_for(lang)
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        try:
            done = await translate_templates(lang, part)
        except Exception as e:
            logger.warning(f"translate {lang} failed: {e}")
            break
        if not done:
            empty += 1
            if empty >= 2:                       # provider keeps failing: stop, the background loop retries later
                logger.warning(f"i18n: {lang}: giving up for now ({last_error or 'provider not answering'})")
                break
            continue
        empty = 0
        for en, tr in done.items():
            _cache.setdefault(lang, {})[key_of(en)] = tr
            _runtime_missing.discard(en)
        await _store(lang, done)
        added += len(done)
    if todo:
        logger.info(f"i18n: {lang}: translated {added}/{len(todo)} templates")
    return added


def start_warm(lang: str) -> Optional[asyncio.Task]:
    if lang in BASE_LANGS or not supported(lang) or get_provider() is None:
        return None
    t = _tasks.get(lang)
    if t and not t.done():
        return t
    _tasks[lang] = asyncio.create_task(warm_language(lang))
    return _tasks[lang]


async def ensure_language(lang: str, wait: float = 20.0) -> bool:
    """Called when a user picks a language: make sure it is (being) translated.
    Waits a little so the very next screen is already in the new language."""
    if lang in BASE_LANGS or not supported(lang):
        return True
    if not missing_for(lang):
        return True
    task = start_warm(lang)
    if task is None:
        return True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=wait)
    except asyncio.TimeoutError:
        return False
    except Exception:
        return False
    return not missing_for(lang)


async def background_loop(interval: int = 600) -> None:
    """Warm every language that real users have chosen, then keep topping up newly seen strings."""
    from sqlalchemy import select, distinct
    from database import AsyncSessionLocal, User
    while True:
        try:
            async with AsyncSessionLocal() as s:
                langs = [r[0] for r in (await s.execute(select(distinct(User.language)))).all()]
            for lang in langs:
                if lang and lang not in BASE_LANGS and supported(lang) and missing_for(lang):
                    await warm_language(lang)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"i18n loop error: {e}")
            await asyncio.sleep(60)
