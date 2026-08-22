"""Loads a policy from source text with a restricted builtins set.

This is a safety net against accidentally-broken generated code (stray
`open()`, `import os`, infinite loops that at least get capped by
GameEngine's max_rounds), not a hard security sandbox — the model is trusted
enough to run locally under your own API keys, same trust level as any code
you'd paste into a REPL yourself. Don't point this at untrusted policy files.
"""
from __future__ import annotations

_ALLOWED_NAMES = (
    "len", "range", "min", "max", "sum", "sorted", "enumerate", "zip",
    "map", "filter", "abs", "all", "any", "list", "dict", "set", "tuple",
    "int", "float", "bool", "str", "isinstance", "True", "False", "None",
    "round", "reversed", "divmod", "pow",
    # required for `class X: ...` / staticmethod / method resolution to work at all
    "__build_class__", "staticmethod", "classmethod", "property", "object",
    "super", "type", "__name__",
    # common, harmless exceptions generated code may raise/catch
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "StopIteration", "ZeroDivisionError", "AttributeError",
)

_builtins_ns = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
_SAFE_BUILTINS = {name: _builtins_ns[name] for name in _ALLOWED_NAMES if name in _builtins_ns}


class PolicyLoadError(Exception):
    pass


def load_policy_from_source(source: str):
    """Execs `source` (must define class Policy with choose_action) and
    returns an instance. Raises PolicyLoadError on anything wrong."""
    namespace: dict = {"__builtins__": _SAFE_BUILTINS, "__name__": "<policy>"}
    try:
        exec(compile(source, "<policy>", "exec"), namespace)
    except Exception as e:  # noqa: BLE001 — deliberately broad, this is untrusted code
        raise PolicyLoadError(f"policy source failed to exec: {e}") from e

    policy_cls = namespace.get("Policy")
    if policy_cls is None:
        raise PolicyLoadError("source does not define a `Policy` class")
    try:
        instance = policy_cls()
    except Exception as e:  # noqa: BLE001
        raise PolicyLoadError(f"Policy() constructor failed: {e}") from e
    if not hasattr(instance, "choose_action"):
        raise PolicyLoadError("Policy instance has no choose_action method")
    return instance


def load_policy_from_file(path) -> object:
    from pathlib import Path
    return load_policy_from_source(Path(path).read_text(encoding="utf-8"))
