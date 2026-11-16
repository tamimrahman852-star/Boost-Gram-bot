"""Validate the manual translation files.   python tools/check_locales.py [lang ...]
Checks: every line of locales/lines.json has a translation, and placeholders ({amount}, {v0:,.0f}) and HTML tags
(<b>, <code> ...) are present in the same number as in English. Exit code 1 if anything is wrong."""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BOT_TOKEN", "1:x"); os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("REDIS_URL", "redis://x"); os.environ.setdefault("ADMIN_IDS", "1")
import i18n  # noqa: E402
i2 = i18n

PROT = re.compile(r"\{\{|\}\}|\{[^{}]*\}|</?[a-zA-Z][^>]*>")


def main(args):
    lines = json.load(open(i18n.LINES_PATH, encoding="utf-8"))
    langs = args or i18n.manual_languages()
    bad_total = 0
    for lang in langs:
        tr = i18n.read_tsv(os.path.join(i18n.LOCALES_DIR, lang + ".tsv"))
        missing = [i for i in range(len(lines)) if i not in tr and i2.needs_translation(lines[i])]
        extra = [i for i in tr if i >= len(lines)]
        broken = []
        for i, text in tr.items():
            if i < len(lines) and sorted(PROT.findall(lines[i])) != sorted(PROT.findall(text)):
                broken.append(i)
        status = "OK " if not (missing or extra or broken) else "BAD"
        print(f"{status} {lang}: {len(tr)}/{len(lines)} lines"
              + (f" | missing {len(missing)}: {missing[:12]}" if missing else "")
              + (f" | damaged placeholders/tags {len(broken)}: {broken[:12]}" if broken else "")
              + (f" | unknown indexes {extra[:5]}" if extra else ""))
        for i in broken[:6]:
            print(f"     #{i} EN: {lines[i]}\n          {lang.upper()}: {tr[i]}")
        bad_total += bool(missing or extra or broken)
    return 1 if bad_total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
