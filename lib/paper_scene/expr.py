"""Arithmetic on the turn number of a ``repeat`` (use case 2: lean, duration and pitch
that grow with the turn).

A closed parser, no ``eval``: numbers, ``$n``, ``+ - * /``, parentheses, unary minus,
``min(a, b)`` and ``max(a, b)``. Nothing else: no names, calls, attributes or powers.

    "2*$n+1" -> 7 for n = 3        "min($n, 2)/2" -> 1.0 for n = 3

A string is read as arithmetic only when every character belongs to this little
language; otherwise ``$n`` is replaced as text ("h_$n" -> "h_3", a channel name).
"""

from __future__ import annotations

import math
import re

TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?|\.\d+)|(\$n)|(min|max)|([-+*/(),]))")
ALLOWED = re.compile(r"^[\d\s.$n+\-*/(),miax]+$")


class ExprError(ValueError):
    pass


def is_arithmetic(text: str) -> bool:
    """Characters of the arithmetic language only (then it must parse, or it is an error)."""
    s = text.strip()
    return "$n" in s and bool(ALLOWED.match(s))


def _tokens(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = TOKEN.match(text, pos)
        if not m:
            raise ExprError(f"{text!r}: unexpected {text[pos:pos + 8]!r}")
        num, var, fn, op = m.groups()
        out.append(("num", num) if num else ("var", var) if var else ("fn", fn) if fn else ("op", op))
        pos = m.end()
    return out


def evaluate(text: str, n: int) -> float:
    toks = _tokens(text)
    i = 0

    def peek():
        return toks[i] if i < len(toks) else (None, None)

    def take(kind=None, val=None):
        nonlocal i
        t = peek()
        if t[0] is None or (kind and t[0] != kind) or (val and t[1] != val):
            raise ExprError(f"{text!r}: expected {val or kind}, got {t[1]!r}")
        i += 1
        return t

    def expr():
        v = term()
        while peek() in (("op", "+"), ("op", "-")):
            op = take()[1]
            w = term()
            v = v + w if op == "+" else v - w
        return v

    def term():
        v = factor()
        while peek() in (("op", "*"), ("op", "/")):
            op = take()[1]
            w = factor()
            if op == "/" and w == 0:
                raise ExprError(f"{text!r}: division by zero")
            v = v * w if op == "*" else v / w
        return v

    def factor():
        kind, val = peek()
        if (kind, val) == ("op", "-"):
            take()
            return -factor()
        if kind == "num":
            take()
            return float(val)
        if kind == "var":
            take()
            return float(n)
        if kind == "fn":
            take()
            take("op", "(")
            a = expr()
            take("op", ",")
            b = expr()
            take("op", ")")
            return min(a, b) if val == "min" else max(a, b)
        if (kind, val) == ("op", "("):
            take()
            v = expr()
            take("op", ")")
            return v
        raise ExprError(f"{text!r}: unexpected {val!r}")

    v = expr()
    if i != len(toks):
        raise ExprError(f"{text!r}: unexpected {toks[i][1]!r}")
    if not math.isfinite(v) or abs(v) > 1e6:
        raise ExprError(f"{text!r}: result {v} out of range")
    return int(v) if float(v).is_integer() else v
