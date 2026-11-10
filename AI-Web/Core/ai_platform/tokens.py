"""Token estimation, budgets and conversation compression.

Token counts here are ESTIMATES (characters / ~3.4). Real counts reported by
the provider are always preferred and flagged ``usage_estimated`` otherwise.
"""

import re

CHARS_PER_TOKEN = 3.4


def estimate_tokens(text):
    return int(len(text or "") / CHARS_PER_TOKEN) + 1 if text else 0


def tokens_to_chars(tokens):
    return int(tokens * CHARS_PER_TOKEN)


def _first_sentence(text, limit):
    text = " ".join((text or "").split())
    m = re.search(r"(?<=[.!?])\s", text)
    first = text[:m.start()] if m else text
    return first[:limit]


def compress_history(turns, max_turns=4, summary_chars=600,
                     summarize=True, question_chars=300, answer_chars=500):
    """Turn prior (prompt, response) pairs into a compact context block.

    ``turns`` is ordered oldest -> newest. The newest ``max_turns`` turns are
    kept (trimmed); older ones are reduced to an extractive one-line-per-turn
    summary capped at ``summary_chars``. No LLM call is made, so this costs no
    tokens and cannot invent content.
    """

    turns = [t for t in (turns or []) if (t.get("prompt") or "").strip()]
    if not turns or max_turns <= 0:
        return {"text": "", "turns_used": 0, "summarized": 0}

    recent = turns[-max_turns:]
    older = turns[:-max_turns]

    lines = []
    summarized = 0

    if older and summarize and summary_chars > 0:
        parts = []
        for t in older[-10:]:
            parts.append("Q: " + _first_sentence(t["prompt"], 80) +
                         " -> " + _first_sentence(t.get("response") or "", 80))
        summary = " | ".join(parts)
        if len(summary) > summary_chars:
            summary = summary[:summary_chars].rsplit(" ", 1)[0] + "..."
        lines.append("Earlier in this conversation (summary): " + summary)
        summarized = len(older)

    for t in recent:
        q = " ".join(t["prompt"].split())[:question_chars]
        a = " ".join((t.get("response") or "").split())
        if len(a) > answer_chars:
            a = a[:answer_chars].rsplit(" ", 1)[0] + "..."
        lines.append(f"User: {q}\nAssistant: {a}")

    return {"text": "\n".join(lines), "turns_used": len(recent),
            "summarized": summarized}


_FOLLOW_UP = re.compile(
    r"^(and |also |what about |how about |why |can you |could you |tell me more|"
    r"explain (that|it|this)|more details|elaborate|what else)|"
    r"\b(it|this|that|these|those|they|them|its|the same|above|previous)\b", re.I)


def needs_rewrite(question):
    q = (question or "").strip()
    return bool(q) and len(q) < 140 and bool(_FOLLOW_UP.search(q))


def rewrite_followup(question, turns):
    """Deterministic follow-up expansion for retrieval.

    A short follow-up like "what about its validations?" retrieves nothing
    useful on its own. We append the previous user question (never the
    previous answer) so retrieval has the subject. The displayed question and
    the LLM prompt keep the user's original wording.
    """

    if not needs_rewrite(question):
        return question

    prev = [t for t in (turns or []) if (t.get("prompt") or "").strip()]
    if not prev:
        return question

    return f"{question.strip()} (context: {' '.join(prev[-1]['prompt'].split())[:200]})"
