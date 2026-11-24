"""Resolve the ERB templating found in riscv-unified-db descriptions.

UDB normally evaluates these templates (Ruby ERB) against one processor
configuration. A generic reference has no configuration, so we resolve them
for a "maximal" configuration:

- ``ext?(:X)`` is true for every extension (``!ext?`` / ``unless ext?`` false);
- ``possible_xlens.include?(N)`` is true;
- loops over implemented exception / interrupt codes list every code;
- any other condition (parameter values such as ``MXLEN == 32``) is unknown:
  every branch is kept and labelled with its condition, so nothing is hidden.

Anything else raises, so a new construct in UDB fails the build instead of
leaking template code into the site.
"""

import re

TAG = re.compile(r"<%(-?)(=?)(.*?)(-?)%>", re.S)
EXT = re.compile(r"ext\?\(\s*:\w+\s*(?:,\s*\"[^\"]*\"\s*)?\)")


class Unknown(Exception):
    pass


def tokenize(src):
    """Split into ('text', s) and ('code'|'out', s), applying ERB trim rules.

    A non-output tag alone on its line removes that whole line; ``-%>``
    removes the newline that follows; ``<%-`` removes indentation before it.
    """
    toks = []
    pos = 0
    for m in TAG.finditer(src):
        start, end = m.start(), m.end()
        is_out = m.group(2) == "="
        code = m.group(3).strip()
        line_start = src.rfind("\n", 0, start) + 1
        nl = src.find("\n", end)
        line_end = len(src) if nl < 0 else nl
        alone = src[line_start:start].strip() == "" and src[end:line_end].strip() == ""
        text = src[pos:start]
        if not is_out and alone:
            text = src[pos:line_start]
            end = line_end + 1 if nl >= 0 else line_end
        else:
            if m.group(1) == "-":
                text = text[: len(text.rstrip(" \t"))]
            if m.group(4) == "-" and src[end:end + 1] == "\n":
                end += 1
        toks.append(("text", text))
        toks.append(("out" if is_out else "code", code))
        pos = end
    toks.append(("text", src[pos:]))
    return toks


def parse(toks, i=0, stop=("end",)):
    """Build a tree: list of nodes; returns (nodes, index, stopping keyword)."""
    nodes = []
    while i < len(toks):
        kind, s = toks[i]
        if kind == "text":
            nodes.append(("text", s))
            i += 1
            continue
        if kind == "out":
            nodes.append(("out", s))
            i += 1
            continue
        word = s.split()[0] if s.split() else ""
        if word in ("end", "else", "elsif") and word in stop:
            return nodes, i, word
        if word in ("if", "unless"):
            branches = []
            cond = s[len(word):].strip()
            if word == "unless":
                cond = f"!({cond})"
            body, i, kw = parse(toks, i + 1, ("end", "else", "elsif"))
            branches.append((cond, body))
            while kw in ("elsif", "else"):
                c = toks[i][1]
                cond = c[len("elsif"):].strip() if kw == "elsif" else None
                body, i, kw = parse(toks, i + 1, ("end", "else", "elsif"))
                branches.append((cond, body))
            nodes.append(("if", branches))
            i += 1
            continue
        m = re.match(r"(\w+)\.sort_by\s*\{.*\}\.each do \|(\w+)\|$", s)
        if m:
            body, i, _ = parse(toks, i + 1, ("end",))
            nodes.append(("each", m.group(1), m.group(2), body))
            i += 1
            continue
        m = re.match(r"(\w+)\s*=\s*(.+)$", s)
        if m and "==" not in s:
            nodes.append(("assign", m.group(1), m.group(2)))
            i += 1
            continue
        raise ValueError(f"unsupported ERB tag: <% {s} %>")
    return nodes, i, None


def split_top(s, ch):
    depth = 0
    for k, c in enumerate(s):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ch and depth == 0:
            # a ternary "?" is surrounded by spaces; "ext?(" is not one
            if ch == "?" and not (s[k - 1:k] == " " and s[k + 1:k + 2] == " "):
                continue
            return s[:k], s[k + 1:]
    return None


def strip_outer(s):
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        for k, c in enumerate(s):
            depth += c == "("
            depth -= c == ")"
            if depth == 0 and k < len(s) - 1:
                return s
        s = s[1:-1].strip()
    return s


def cond_value(c, env):
    """True / False, or raise Unknown."""
    c = strip_outer(c)
    if c.startswith("!") and not c.startswith("!="):
        return not cond_value(c[1:], env)
    if EXT.fullmatch(c):
        return True
    if re.fullmatch(r"possible_xlens\.include\?\(\d+\)", c):
        return True
    for op in ("&&", "||"):
        parts = split_top(c, op[0])
        if parts and parts[1].startswith(op[1]):
            a, b = cond_value(parts[0], env), cond_value(parts[1][1:], env)
            return (a and b) if op == "&&" else (a or b)
    raise Unknown(c)


class Alt(list):
    """A value that depends on which extensions are implemented:
    a list of (condition label, value), shown with every alternative."""

    def __str__(self):
        parts = [f"{v} {lab}" for lab, v in self[:-1]] + [f"otherwise {self[-1][1]}"]
        return "(" + ", ".join(parts) + ")"


def is_ext_cond(c):
    return "ext?" in c


def atom(c, neg=False):
    """Readable label for one condition: "with H", "without C", "`MXLEN == 32`"."""
    c = strip_outer(c)
    if c.startswith("!") and not c.startswith("!="):
        return atom(c[1:], not neg)
    m = EXT.fullmatch(c)
    if m:
        name = re.search(r":(\w+)", c).group(1)
        return ("without " if neg else "with ") + name
    return ("not " if neg else "") + f"`{c}`"


def label(prev, cond):
    parts = [atom(p, True) for p in prev]
    if cond is not None:
        parts.append(atom(cond))
    if not parts:
        return "Otherwise"
    text = ", ".join(parts)
    if all(p.startswith(("with ", "without ")) for p in parts):
        return text[0].upper() + text[1:]
    return "When " + text


def expr_value(e, env):
    e = strip_outer(e)
    q = split_top(e, "?")
    if q:
        rest = split_top(q[1], ":")
        if not rest:
            raise ValueError(f"bad ternary: {e}")
        if is_ext_cond(q[0]):  # keep every alternative
            yes, no = expr_value(rest[0], env), expr_value(rest[1], env)
            alts = Alt([(atom(q[0]), yes)])
            alts.extend(no if isinstance(no, Alt) else [("", no)])
            return alts
        return expr_value(rest[0], env) if cond_value(q[0], env) else expr_value(rest[1], env)
    m = re.fullmatch(r"(\w+)\s*([-+])\s*(\d+)", e)
    if m and m.group(1) in env:
        v, k = env[m.group(1)], int(m.group(3))
        f = (lambda x: int(x) - k) if m.group(2) == "-" else (lambda x: int(x) + k)
        return Alt((lab, f(x)) for lab, x in v) if isinstance(v, Alt) else f(v)
    if re.fullmatch(r"\d+", e):
        return int(e)
    m = re.fullmatch(r"(\w+)\.(\w+)", e)
    if m and m.group(1) in env:
        return env[m.group(1)][m.group(2)]
    if e in env:
        return env[e]
    raise ValueError(f"unsupported ERB expression: <%= {e} %>")


def render(nodes, env, codes):
    out = []
    for n in nodes:
        if n[0] == "text":
            out.append(n[1])
        elif n[0] == "out":
            out.append(str(expr_value(n[1], env)))
        elif n[0] == "assign":
            env[n[1]] = expr_value(n[2], env)
        elif n[0] == "each":
            for item in sorted(codes[n[1]], key=lambda x: x["num"]):
                out.append(render(n[3], dict(env, **{n[2]: item}), codes))
        elif n[0] == "if":
            out.append(render_if(n[1], env, codes))
    return "".join(out)


def render_if(branches, env, codes):
    """The first branch, when it holds in the maximal configuration, is shown
    as is; every other branch is kept, labelled with its condition."""
    out, prev = [], []
    for k, (cond, body) in enumerate(branches):
        if k == 0:
            try:
                holds = cond_value(cond, env)
            except Unknown:
                holds = False
            if holds:
                out.append(render(body, env, codes))
                prev.append(cond)
                continue
        text = render(body, env, codes).strip()
        if text and "\n\n" not in text and len(text) < 400:
            # a fragment of a sentence or a single line: keep it in the flow
            out.append(f" [_{label(prev, cond)}:_ {' '.join(text.split())}] ")
        elif text:
            out.append(f"\n_{label(prev, cond)}:_\n\n{text}\n\n")
        if cond is not None:
            prev.append(cond)
    return "".join(out)


def resolve(src, codes):
    if "<%" not in src:
        return src
    nodes, _, kw = parse(tokenize(src))
    if kw is not None:
        raise ValueError(f"unbalanced ERB ({kw})")
    return render(nodes, {}, codes)
