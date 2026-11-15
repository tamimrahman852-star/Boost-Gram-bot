"""One-off source transformer used to make every bilingual text translatable.

 1. `X if lang == "bn" else Y`            ->  L(lang, X, Y)
 2. `L(lang, bn, f"...{expr:spec}...")`   ->  L(lang, bn, f"...", _tpl="...{v0:spec}...", _kw={"v0": expr})
    so the *template* (not the formatted sentence) can be machine-translated once and re-used with any numbers.
"""
import ast
import sys


def offsets(src_b: bytes):
    starts, pos = [0], 0
    for line in src_b.split(b"\n"):
        pos += len(line) + 1
        starts.append(pos)
    return starts


def span(starts, node):
    return starts[node.lineno - 1] + node.col_offset, starts[node.end_lineno - 1] + node.end_col_offset


def is_lang_test(t):
    if isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "lang" \
            and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq) \
            and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value == "bn":
        return True
    return isinstance(t, ast.Name) and t.id == "bn"


def pass_ternary(path):
    src = open(path, encoding="utf-8").read()
    b = src.encode()
    tree = ast.parse(src)
    starts = offsets(b)
    edits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.IfExp) and is_lang_test(n.test):
            (s, e) = span(starts, n)
            bs, be = span(starts, n.body)
            os_, oe = span(starts, n.orelse)
            body, other = b[bs:be].decode(), b[os_:oe].decode()
            edits.append((s, e, f"L(lang, {body}, {other})"))
    # drop edits nested inside other edits (apply outermost only; rerun handles the rest)
    edits.sort(key=lambda x: (x[0], -x[1]))
    kept, last_end = [], -1
    for ed in edits:
        if ed[0] >= last_end:
            kept.append(ed)
            last_end = ed[1]
    for s, e, new in sorted(kept, reverse=True):
        b = b[:s] + new.encode() + b[e:]
    open(path, "w", encoding="utf-8").write(b.decode())
    return len(kept)


def template_of(node: ast.JoinedStr, src_b, starts):
    parts, kw, names = [], {}, {}
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(str(v.value).replace("{", "{{").replace("}", "}}"))
        elif isinstance(v, ast.FormattedValue):
            if v.conversion != -1:
                return None
            spec = ""
            if v.format_spec is not None:
                if not all(isinstance(x, ast.Constant) for x in v.format_spec.values):
                    return None
                spec = "".join(str(x.value) for x in v.format_spec.values)
            s, e = span(starts, v.value)
            code = src_b[s:e].decode()
            if code not in names:
                names[code] = f"v{len(names)}"
                kw[names[code]] = code
            parts.append("{" + names[code] + (":" + spec if spec else "") + "}")
        else:
            return None
    return "".join(parts), kw


def pass_templates(path):
    src = open(path, encoding="utf-8").read()
    b = src.encode()
    tree = ast.parse(src)
    starts = offsets(b)
    edits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "L" and len(n.args) == 3 and not n.keywords:
            en = n.args[2]
            if isinstance(en, ast.JoinedStr):
                res = template_of(en, b, starts)
                if res is None:
                    continue
                tpl, kw = res
                _, end = span(starts, en)
                end += 1
                kws = "{" + ", ".join(f'"{k}": {v}' for k, v in kw.items()) + "}"
                edits.append((end - 1, f", _tpl={tpl!r}, _kw={kws}"))
    for pos, text in sorted(edits, reverse=True):
        b = b[:pos] + text.encode() + b[pos:]
    open(path, "w", encoding="utf-8").write(b.decode())
    return len(edits)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        t = pass_ternary(p)
        while True:                 # nested ternaries: repeat until stable
            more = pass_ternary(p)
            if not more:
                break
            t += more
        n = pass_templates(p)
        print(f"{p}: ternaries->L {t}, templates added {n}")
