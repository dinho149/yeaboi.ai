"""Shared Python ↔ Go parity comparison helpers.

Used by every parity suite. Dicts compare key-wise (JSON object order is
asserted separately where it is contractual); lists are order-sensitive;
floats tolerate the sub-1e-9 wobble between Python and Go rounded values.
"""

from __future__ import annotations


def approx_equal(a, b, path=""):
    """Recursive equality with float tolerance; returns a list of diffs."""
    diffs = []
    if isinstance(a, float) or isinstance(b, float):
        if abs(float(a) - float(b)) > 1e-9:
            diffs.append(f"{path}: {a!r} != {b!r}")
    elif isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                diffs.append(f"{path}.{key}: present in only one side")
            else:
                diffs.extend(approx_equal(a[key], b[key], f"{path}.{key}"))
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            diffs.append(f"{path}: length {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diffs.extend(approx_equal(x, y, f"{path}[{i}]"))
    elif a != b:
        diffs.append(f"{path}: {a!r} != {b!r}")
    return diffs


def key_orders(value, path=""):
    """Every JSON object's key order in the tree, as {path: [keys]}.

    The float-tolerant differ compares dicts key-wise, which deliberately
    ignores order — but for wire shapes whose object order is contractual
    (standup.aggregate's member-keyed maps feed the LLM prompt's json.dumps),
    the suites compare this projection too.
    """
    orders = {}
    if isinstance(value, dict):
        orders[path or "$"] = list(value)
        for key, child in value.items():
            orders.update(key_orders(child, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for i, child in enumerate(value):
            orders.update(key_orders(child, f"{path}[{i}]"))
    return orders
