"""Minimal JSON-Schema subset validator for MCP tool arguments.

Supports: type (object/string/number/integer/boolean/array/null), required,
properties, enum, minimum/maximum, minLength/maxLength, items, and rejects
unknown properties when additionalProperties is false. Anything it does not
understand is ignored (never relaxed into acceptance of a wrong type).
"""

_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}

MAX_STRING = 8000
MAX_DEPTH = 6


def validate(value, schema, path="arguments", depth=0):
    """Return a list of error strings (empty when valid)."""

    errors = []
    if depth > MAX_DEPTH:
        return [f"{path}: nesting too deep"]
    if not isinstance(schema, dict):
        return errors

    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES.get(x, lambda v: True)(value) for x in types):
            return [f"{path}: expected {'/'.join(types)}"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")

    if isinstance(value, str):
        if len(value) > min(int(schema.get("maxLength", MAX_STRING)), MAX_STRING):
            errors.append(f"{path}: too long")
        if len(value) < int(schema.get("minLength", 0)):
            errors.append(f"{path}: too short")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")

    if isinstance(value, list):
        if len(value) > 200:
            errors.append(f"{path}: too many items")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value[:200]):
                errors += validate(item, item_schema, f"{path}[{i}]", depth + 1)

    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for req in schema.get("required") or []:
            if req not in value:
                errors.append(f"{path}.{req}: required")
        if schema.get("additionalProperties") is False:
            for k in value:
                if k not in props:
                    errors.append(f"{path}.{k}: not allowed")
        for k, v in value.items():
            if k in props:
                errors += validate(v, props[k], f"{path}.{k}", depth + 1)

    return errors
