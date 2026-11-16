"""Collect every English UI text (templates) into locales/catalog.json.
Run:  python tools/extract_catalog.py        (re-run after adding new texts)
"""
import ast
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BOT_TOKEN", "1:x"); os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("REDIS_URL", "redis://x"); os.environ.setdefault("ADMIN_IDS", "1")

BN = re.compile("[\u0980-\u09FF]")


def const_str(n):
    return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None


def consts(n):
    """All string constants an expression can evaluate to ('ON' if x else 'OFF' -> both)."""
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return [n.value]
    if isinstance(n, ast.IfExp):
        return consts(n.body) + consts(n.orelse)
    return []


def used_keys():
    keys = set()
    for f in glob.glob(os.path.join(ROOT, "*.py")) + glob.glob(os.path.join(ROOT, "handlers", "*.py")):
        if f.endswith("localization.py"):
            continue
        src = open(f, encoding="utf-8").read()
        for k in __import__("localization").L10N.T["en"]:
            if re.search(r"[\"']" + re.escape(k) + r"[\"']", src):
                keys.add(k)
    return keys


def main():
    from localization import L10N
    out, seen = [], set()

    def add(t):
        if t and any(c.isalpha() for c in t) and t not in seen:
            seen.add(t)
            out.append(t)

    used = used_keys()
    for k, v in L10N.T["en"].items():        # main menu / cabinet texts first (most used); skip keys no code uses
        if k in used:
            add(v)
    files = sorted(glob.glob(os.path.join(ROOT, "handlers", "*.py"))) + [os.path.join(ROOT, f) for f in ("keyboards.py", "main.py", "workers.py", "services.py", "helpers.py")]
    for f in files:
        tree = ast.parse(open(f, encoding="utf-8").read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                if getattr(n.func, "id", None) == "L" and len(n.args) >= 3:
                    tpl = next((const_str(k.value) for k in n.keywords if k.arg == "_tpl"), None)
                    if tpl is not None:
                        add(tpl)
                    else:
                        for c in consts(n.args[2]):
                            add(c)
                    continue
                elts = n.args
            elif isinstance(n, (ast.Tuple, ast.List)):
                elts = n.elts
            else:
                continue
            for a, b in zip(elts, elts[1:]):      # (Bengali text, English text) pairs used by helpers/tables
                sa, sb = const_str(a), const_str(b)
                if sa and sb and BN.search(sa) and not BN.search(sb):
                    add(sb)
    os.makedirs(os.path.join(ROOT, "locales"), exist_ok=True)
    with open(os.path.join(ROOT, "locales", "catalog.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    # lines.json is APPEND-ONLY: translation files (locales/<lang>.tsv) refer to lines by their index
    lp = os.path.join(ROOT, "locales", "lines.json")
    try:
        lines = json.load(open(lp, encoding="utf-8"))
    except Exception:
        lines = []
    known = set(lines)
    for t in out:
        for ln in t.split("\n"):
            if ln.strip() and ln not in known:
                known.add(ln)
                lines.append(ln)
    with open(lp, "w", encoding="utf-8") as fh:
        json.dump(lines, fh, ensure_ascii=False, indent=0)
    print(f"catalog: {len(out)} texts | lines.json: {len(lines)} lines")


if __name__ == "__main__":
    main()
