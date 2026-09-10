"""Making MCP tool schemas acceptable to each provider.

MCP tools carry ordinary JSON Schema. Providers accept subsets of it, and they
differ in which subset: Gemini's function declarations reject several keywords
outright, while OpenAI-compatible endpoints are more permissive. Narrowing the
schema is lossy, so it happens here, once per adapter, rather than by watering
down the tools themselves.
"""

from __future__ import annotations

from typing import Any

# Keys Gemini's function-declaration schema does not accept.
_GEMINI_STRIP = {
    "additionalProperties",
    "$schema",
    "$ref",
    "$defs",
    "definitions",
    "examples",
    "const",
    "patternProperties",
    "allOf",
    "oneOf",
    "not",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "default",
}

_GEMINI_TYPES = {"string", "number", "integer", "boolean", "array", "object"}


def sanitise_for_gemini(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Reduce a JSON Schema to what Gemini accepts, without losing meaning.

    Optionality expressed as ``anyOf: [T, null]`` becomes a plain ``T`` that is
    simply absent from ``required``, which is how Gemini expresses the same idea.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _GEMINI_STRIP:
            continue
        if key == "anyOf":
            variants = [v for v in value if isinstance(v, dict)]
            non_null = [v for v in variants if v.get("type") != "null"]
            if len(non_null) == 1:
                merged = sanitise_for_gemini(non_null[0])
                for k, v in merged.items():
                    out.setdefault(k, v)
                continue
            # More than one real variant cannot be expressed; accept anything.
            out["type"] = "string"
            continue
        if key == "properties" and isinstance(value, dict):
            out["properties"] = {k: sanitise_for_gemini(v) for k, v in value.items()}
            continue
        if key == "items":
            out["items"] = (
                sanitise_for_gemini(value) if isinstance(value, dict) else {"type": "string"}
            )
            continue
        if key == "type":
            if isinstance(value, list):
                real = [t for t in value if t != "null"]
                out["type"] = real[0] if real else "string"
            elif value in _GEMINI_TYPES:
                out["type"] = value
            continue
        out[key] = value

    if "type" not in out:
        out["type"] = "object" if "properties" in out else "string"
    if out["type"] == "object" and "properties" not in out:
        out["properties"] = {}
    if out["type"] == "array" and "items" not in out:
        out["items"] = {"type": "string"}
    return out


def sanitise_for_openai(schema: dict[str, Any] | None) -> dict[str, Any]:
    """OpenAI-compatible endpoints accept ordinary JSON Schema with light tidying."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    out = dict(schema)
    out.pop("$schema", None)
    out.setdefault("type", "object")
    if out["type"] == "object":
        out.setdefault("properties", {})
    return out
