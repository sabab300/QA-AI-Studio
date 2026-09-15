# Create: AI-Web/Core/secret_masking.py

"""
QA AI Studio
Secret Masking

Version: 1.0

QA-AI-STUDIO-API-COLLECTION-KNOWLEDGE-QA-ENGINEERING-API-AUTOMATION-FINAL-FIX
item 11: a client secret, bearer token, API key, password, or an
already-resolved Authorization value must never be emitted unmasked
into a vector-store embedding, an AI prompt, or an application log.

A value that is itself a Postman-style "{{variableName}}" placeholder
is left untouched -- it names a variable, it is not the secret's
literal value (e.g. "X-Token: {{X-Token}}" is fine to show as-is;
"X-Client-Secret: 8f3c...", a resolved literal, is not).
"""

import re

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(secret|token|password|passwd|api[-_]?key|apikey|authoriz|"
    r"auth[-_]?key|access[-_]?key|private[-_]?key|client[-_]?secret|"
    r"session[-_]?id|cookie|bearer|credential)",
    re.IGNORECASE,
)

_VARIABLE_REF_PATTERN = re.compile(r"^\s*\{\{.*\}\}\s*$")

_SCHEME_PREFIX_PATTERN = re.compile(
    r"^(Bearer|Basic|Token|Digest|OAuth)\s+(.+)$", re.IGNORECASE
)

MASK = "******"


def is_variable_reference(value):
    """True for a literal "{{variableName}}" placeholder (a name, not a secret)."""

    return bool(_VARIABLE_REF_PATTERN.match(str(value or "")))


def is_sensitive_key(key):
    """True when a header/field NAME looks like it carries a credential."""

    return bool(_SENSITIVE_KEY_PATTERN.search(str(key or "")))


def mask_value(key, value):
    """
    Returns `value` unchanged unless `key` looks sensitive AND `value`
    is a resolved literal (not a "{{variable}}" reference) -- in which
    case the credential portion is replaced with "******". A
    "Bearer <token>" style value keeps its scheme word and masks only
    the token.
    """

    text = "" if value is None else str(value)

    if not text or is_variable_reference(text) or not is_sensitive_key(key):

        return text

    match = _SCHEME_PREFIX_PATTERN.match(text)

    if match and not is_variable_reference(match.group(2)):

        return f"{match.group(1)} {MASK}"

    return MASK


def mask_headers(headers):
    """headers: iterable of {"key": ..., "value": ...} -> same shape, masked."""

    masked = []

    for header in headers or []:

        key = header.get("key", "") if isinstance(header, dict) else ""

        value = header.get("value", "") if isinstance(header, dict) else header

        masked.append({"key": key, "value": mask_value(key, value)})

    return masked


def mask_header_line(key, value):
    """Convenience for "Key: value" text lines built for prompts/logs."""

    return f"{key}: {mask_value(key, value)}"


def mask_auth_details(auth_details):
    """
    Postman's request.auth block (e.g. {"type": "bearer", "bearer":
    [{"key": "token", "value": "..."}]}) -- mask every nested value
    field whose key looks sensitive, recursively, without assuming a
    fixed shape (different auth types nest differently).
    """

    if isinstance(auth_details, dict):

        result = {}

        for key, value in auth_details.items():

            if key == "value" and "key" in auth_details:

                # Handled by the caller via mask_value(auth_details["key"], value)
                result[key] = value

            else:

                result[key] = mask_auth_details(value)

        if "key" in result and "value" in result:

            result["value"] = mask_value(result.get("key"), result.get("value"))

        return result

    if isinstance(auth_details, list):

        return [mask_auth_details(item) for item in auth_details]

    return auth_details
