"""Shared semantic normalization for Playwright recording, validation, and repair."""

import re


_INSTRUCTION_PREFIXES = {
    "please", "enter", "select", "choose", "click", "open", "type", "pick",
}
_NOISE_WORDS = {"the", "a", "an", "field", "control", "input", "value"}


def semantic_tokens(value):
    """Return conservative logical-name tokens across common UI naming styles."""
    text = str(value or "").strip()
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    words = re.findall(r"[a-z0-9]+", text.lower())
    while words and words[0] in _INSTRUCTION_PREFIXES:
        words.pop(0)
    return tuple(word for word in words if word not in _NOISE_WORDS)


def semantic_name_compatible(expected, evidence):
    """Require one strong equivalent or substantial token agreement.

    `evidence` should contain persisted/DOM facts for one control (name, id,
    placeholder, label, aria-label, accessible name), not nearby page text.
    """
    expected_tokens = semantic_tokens(expected)
    if not expected_tokens:
        return True
    expected_set = set(expected_tokens)
    for value in evidence or ():
        actual_tokens = semantic_tokens(value)
        if not actual_tokens:
            continue
        actual_set = set(actual_tokens)
        if actual_tokens == expected_tokens:
            return True
        overlap = expected_set & actual_set
        if overlap and (
            overlap == expected_set
            or overlap == actual_set
            or len(overlap) >= max(2, min(len(expected_set), len(actual_set)) - 1)
        ):
            return True
    return False
