"""Read Lua data files (WoW SavedVariables, Blizzard API docs) without executing them.

Handles top-level assignments `Name = value` and `local Name = value`, tables with `key = v`, `["key"] = v`,
`[1] = v` and positional entries, strings with Lua escapes, numbers, true/false/nil and comments.
Tables with only positional entries become lists; anything else becomes a dict whose numeric keys are ints
(positional entries get keys 1..n). Use `entries()` to walk either form.
"""
import re

TOKEN = re.compile(r"""
    (?P<ws>\s+|--\[(?P<eq>=*)\[.*?\](?P=eq)\]|--[^\n]*)
  | (?P<str>"[^"\\]*(?:\\.[^"\\]*)*"|'[^'\\]*(?:\\.[^'\\]*)*')
  | (?P<lstr>\[(?P<leq>=*)\[.*?\](?P=leq)\])
  | (?P<num>-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?))
  | (?P<name>[A-Za-z_]\w*)
  | (?P<p>[{}=,;\[\]])
""", re.S | re.X)

ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
           "\\": "\\", '"': '"', "'": "'", "\n": "\n"}


ESCAPE = re.compile(r"\\(\d{1,3}|.)", re.S)


def unescape(body):
    if "\\" not in body:
        return body
    return ESCAPE.sub(lambda m: chr(int(m.group(1))) if m.group(1).isdigit() else ESCAPES.get(m.group(1), m.group(1)),
                      body)


def tokenize(text):
    toks = []
    for m in TOKEN.finditer(text):
        kind = m.lastgroup
        if m.group("ws") is not None:
            continue
        if m.group("str") is not None:
            toks.append(("str", unescape(m.group("str")[1:-1])))
        elif m.group("lstr") is not None:
            s = m.group("lstr")
            n = len(m.group("leq"))
            toks.append(("str", s[n + 2:-(n + 2)].lstrip("\n")))
        elif m.group("num") is not None:
            s = m.group("num")
            toks.append(("num", int(s, 16) if s.lower().lstrip("-").startswith("0x")
                         else float(s) if any(ch in s for ch in ".eE") else int(s)))
        elif m.group("name") is not None:
            toks.append(("name", m.group("name")))
        else:
            toks.append(("p", m.group("p")))
    return toks


def parse_value(toks, i):
    kind, tok = toks[i]
    if kind == "p" and tok == "{":
        positional, keyed, i = [], {}, i + 1
        while not (toks[i][0] == "p" and toks[i][1] == "}"):
            if toks[i][0] == "name" and toks[i + 1] == ("p", "="):
                keyed[toks[i][1]], i = parse_value(toks, i + 2)
            elif toks[i] == ("p", "["):
                key, i = parse_value(toks, i + 1)
                if key is True or key is False:
                    key = str(key).lower()
                keyed[key], i = parse_value(toks, i + 2)  # skip "]" and "="
            else:
                val, i = parse_value(toks, i)
                positional.append(val)
            if toks[i][0] == "p" and toks[i][1] in (",", ";"):
                i += 1
        if not keyed:
            return positional, i + 1
        for n, val in enumerate(positional, 1):
            keyed.setdefault(n, val)
        return keyed, i + 1
    if kind in ("str", "num"):
        return tok, i + 1
    if kind == "name":
        return {"true": True, "false": False, "nil": None}.get(tok, tok), i + 1
    raise ValueError(f"unexpected token {tok!r} at {i}")


def loads(text):
    """All top-level `Name = value` assignments in a Lua data file, as {Name: value}."""
    toks, out, i = tokenize(text), {}, 0
    while i < len(toks):
        if toks[i] == ("name", "local"):
            i += 1
        if toks[i][0] == "name" and i + 1 < len(toks) and toks[i + 1] == ("p", "="):
            out[toks[i][1]], i = parse_value(toks, i + 2)
        else:
            i += 1  # e.g. a trailing function call such as APIDocumentation:AddDocumentationTable(X)
        if i < len(toks) and toks[i][0] == "p" and toks[i][1] == ";":
            i += 1
    return out


def load(path):
    """loads() for a file. SavedVariables strings can hold arbitrary bytes (e.g. compressed data), so
    invalid UTF-8 is kept as surrogate escapes instead of failing."""
    with open(path, "rb") as f:
        return loads(f.read().decode("utf-8", errors="surrogateescape"))


def entries(t):
    """(key, value) pairs of a parsed table, whether it came back as a list (1-based keys) or a dict."""
    if t is None:
        return []
    if isinstance(t, list):
        return list(enumerate(t, 1))
    return list(t.items())


def get(t, key, default=None):
    """t[key] for a parsed table, where numeric keys may be list positions."""
    if isinstance(t, list):
        return t[key - 1] if isinstance(key, int) and 1 <= key <= len(t) else default
    if isinstance(t, dict):
        return t.get(key, default)
    return default
