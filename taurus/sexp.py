"""Minimal S-expression parser/writer for KiCad files."""
from __future__ import annotations

import re
from typing import List, Union

SExp = Union[str, List["SExp"]]

_TOKEN_RE = re.compile(r"""
    (?P<open>\()                     |
    (?P<close>\))                    |
    (?P<quoted>"(?:[^"\\]|\\.)*")    |
    (?P<atom>[^\s()"]+)              |
    (?P<ws>\s+)
""", re.VERBOSE)


def parse(text: str) -> List[SExp]:
    tokens = _TOKEN_RE.finditer(text)
    stack: List[List[SExp]] = [[]]
    for m in tokens:
        kind = m.lastgroup
        if kind == "ws":
            continue
        if kind == "open":
            new: List[SExp] = []
            stack[-1].append(new)
            stack.append(new)
        elif kind == "close":
            if len(stack) <= 1:
                raise ValueError("Unbalanced closing parenthesis")
            stack.pop()
        elif kind == "quoted":
            raw = m.group()[1:-1]
            raw = raw.replace('\\"', '"').replace("\\\\", "\\")
            stack[-1].append(raw)
        elif kind == "atom":
            stack[-1].append(m.group())
        else:
            continue
    if len(stack) != 1:
        raise ValueError("Unbalanced opening parenthesis")
    return stack[0]


def dumps(node: SExp, indent: int = 0) -> str:
    if isinstance(node, str):
        if _needs_quoting(node):
            escaped = node.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        return node
    if not node:
        return "()"
    tag = node[0] if isinstance(node[0], str) else None
    children = node[1:] if tag else node
    tab = "\t" * indent
    child_tab = "\t" * (indent + 1)

    if _is_flat(node):
        inner = " ".join(dumps(c) for c in node)
        return f"({inner})"

    parts = [f"({dumps(node[0])}"] if tag else ["("]
    for child in children:
        parts.append(f"{child_tab}{dumps(child, indent + 1)}")
    parts.append(f"{tab})")
    return "\n".join(parts)


def _needs_quoting(s: str) -> bool:
    if not s:
        return True
    if s[0].isdigit() or s[0] == '-':
        return False
    if any(c in s for c in ' ()"\\'):
        return True
    return False


def _is_flat(node: SExp) -> bool:
    if isinstance(node, str):
        return True
    if len(node) > 6:
        return False
    return all(isinstance(c, str) for c in node)


def find(tree: SExp, tag: str) -> SExp | None:
    if isinstance(tree, list):
        if tree and tree[0] == tag:
            return tree
        for child in tree:
            result = find(child, tag)
            if result is not None:
                return result
    return None


def find_all(tree: SExp, tag: str) -> List[SExp]:
    results = []
    if isinstance(tree, list):
        if tree and tree[0] == tag:
            results.append(tree)
        for child in tree:
            results.extend(find_all(child, tag))
    return results


def get_value(tree: SExp, tag: str) -> str | None:
    node = find(tree, tag)
    if node and len(node) >= 2 and isinstance(node[1], str):
        return node[1]
    return None


def get_property(tree: SExp, prop_name: str) -> str | None:
    for node in find_all(tree, "property"):
        if len(node) >= 3 and node[1] == prop_name:
            return node[2] if isinstance(node[2], str) else None
    return None
