"""Turn an upstream JSON Schema into a Python callable signature.

Aegis re-publishes each upstream tool under its own namespace. The agent must
see the same parameters the upstream declared, so the gateway compiles the
upstream's JSON Schema into a real function signature rather than accepting a
loose bag of arguments. A tool whose schema Aegis cannot express faithfully
still works: the unmapped parameter is typed permissively rather than dropped,
so the call is never silently narrowed.
"""

from __future__ import annotations

import inspect
import keyword
from typing import Any

_PRIMITIVES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}

_SENTINEL = inspect.Parameter.empty


def _annotation_for(spec: dict[str, Any]) -> Any:
    """Best-effort mapping from a JSON Schema node to a Python annotation."""
    if not isinstance(spec, dict):
        return Any

    # anyOf/oneOf carrying null is how optionality is usually expressed.
    for key in ("anyOf", "oneOf"):
        if key in spec:
            variants = [v for v in spec[key] if isinstance(v, dict)]
            non_null = [v for v in variants if v.get("type") != "null"]
            has_null = len(non_null) != len(variants)
            if len(non_null) == 1:
                inner = _annotation_for(non_null[0])
                return inner | None if has_null else inner
            return Any

    declared = spec.get("type")
    if isinstance(declared, list):
        non_null = [t for t in declared if t != "null"]
        if len(non_null) == 1:
            inner = _annotation_for({**spec, "type": non_null[0]})
            return inner | None if len(non_null) != len(declared) else inner
        return Any

    if declared in _PRIMITIVES:
        return _PRIMITIVES[declared]
    if declared == "array":
        item = spec.get("items")
        return list[_annotation_for(item)] if isinstance(item, dict) else list[Any]
    if declared == "object":
        return dict[str, Any]
    return Any


def _safe_name(name: str) -> str:
    candidate = (
        name
        if name.isidentifier()
        else "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    )
    if not candidate or candidate[0].isdigit():
        candidate = f"p_{candidate}"
    if keyword.iskeyword(candidate):
        candidate = f"{candidate}_"
    return candidate


def build_signature(
    schema: dict[str, Any],
) -> tuple[inspect.Signature, dict[str, Any], dict[str, str]]:
    """Compile a JSON Schema object into a signature.

    Returns the signature, the annotations mapping, and a map from the Python
    parameter name back to the wire name, which differ only when a schema uses a
    property name that is not a valid Python identifier.
    """
    properties: dict[str, Any] = (schema or {}).get("properties") or {}
    required: set[str] = set((schema or {}).get("required") or [])

    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    wire_names: dict[str, str] = {}

    # Required parameters first: a signature may not put a defaulted parameter
    # before a non-defaulted one.
    ordered = sorted(properties.items(), key=lambda kv: kv[0] not in required)

    for wire_name, spec in ordered:
        py_name = _safe_name(wire_name)
        while py_name in annotations:
            py_name = f"{py_name}_"
        wire_names[py_name] = wire_name

        annotation = _annotation_for(spec if isinstance(spec, dict) else {})
        if wire_name in required:
            default: Any = _SENTINEL
        elif isinstance(spec, dict) and "default" in spec:
            default = spec["default"]
        else:
            default = None
            annotation = annotation | None if annotation is not Any else Any

        parameters.append(
            inspect.Parameter(
                py_name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=annotation,
                default=default,
            )
        )
        annotations[py_name] = annotation

    annotations["return"] = dict[str, Any]
    return (
        inspect.Signature(parameters, return_annotation=dict[str, Any]),
        annotations,
        wire_names,
    )
