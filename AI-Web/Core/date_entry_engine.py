"""Generic, application-independent date entry and verification for replay.

Why this module exists
----------------------
Replay used to enter every recorded date with ``locator.fill('<text>')`` and
then require ``input_value() == '<text>'``.  Both halves are wrong for real
date controls:

* ``fill()`` writes the value and fires one synthetic ``input`` event.  It
  never produces the key events that *input masks* (ASP.NET MaskedEdit,
  jQuery Masked Input, Cleave/IMask style widgets ...) listen to, so a mask
  that owns the field re-renders its prompt template around the injected text
  and the field ends up in a corrupted, uncommitted state such as
  ``01/10/2026__/__/____``.  A person typing the same date gets ``01/10/2026``.
* Plain string equality cannot tell "the application shows the date I
  entered, formatted its own way" from "the field holds leftover mask slots,
  a partial date or a different date".

What it does
------------
Entry (``enter_date_value``)  - one deliberate action chosen from the live
field itself, never from application names, ids or formats:

* ``<input type=date>``: Playwright's native ISO ``fill`` (the browser's own
  contract), converting the recorded text with the *unambiguous* date it
  represents (format hint, then the browser's own display order).
* every other text-like input: real keyboard entry (focus, select the
  current text, clear, type) so any mask/format handler processes the
  value exactly as it does for a person.

Verification (``compare_date_values``) compares what the field *actually
holds* with what was entered, as a date:

* same calendar date in any display format  -> verified (``01/10/2026`` ==
  ``2026-10-01`` == ``1 Oct 2026``, but ``01/10/2026`` != ``10/01/2026``)
* the date followed by unfilled mask slots  -> NOT verified (``residue``)
* only some components filled               -> NOT verified (``incomplete``)
* a different date / not a date / empty     -> NOT verified
* an expected value that is itself not a valid date (a deliberate negative
  test) is compared literally; it is never "corrected" or normalised.

Nothing here strips mask characters to manufacture a pass, and nothing
assigns ``element.value`` directly.
"""
import calendar as _calendar
import re
import time
from datetime import date

from Core.calendar_engine import date_format

# ---------------------------------------------------------------- vocabulary
_MONTHS = {}
for _i in range(1, 13):
    _MONTHS[_calendar.month_name[_i].lower()] = _i
    _MONTHS[_calendar.month_abbr[_i].lower()] = _i
_MONTHS["sept"] = 9
_WEEKDAYS = {name.lower() for name in list(_calendar.day_name) + list(_calendar.day_abbr)}
_ORDINALS = {"st", "nd", "rd", "th"}
# Characters input masks use for a not-yet-filled slot.
_PROMPT_CHARS = "_#*•●□ ?"
_TOKEN = re.compile(
    r"\d+|[^\W\d_]+|[" + re.escape(_PROMPT_CHARS) + r"]+|\s+|[/\-.,:]|.",
    re.UNICODE,
)
_NUMERIC_SEPARATORS = {"/", "-", ".", " "}


class DateEntryError(ValueError):
    """The recorded text cannot be entered into the live control as a date."""


# ------------------------------------------------------------------- parsing
def _tokens(text):
    out = []
    for raw in _TOKEN.findall(str(text or "")):
        if raw.isdigit():
            out.append(("num", raw))
        elif raw.isspace():
            out.append(("space", raw))
        elif raw in {"/", "-", ".", ",", ":"}:
            out.append(("sep", raw))
        elif raw[0] in _PROMPT_CHARS:
            out.append(("prompt", raw))
        elif raw[0].isalpha():
            lowered = raw.lower()
            if lowered in _MONTHS:
                out.append(("month", lowered))
            elif lowered in _WEEKDAYS:
                out.append(("weekday", lowered))
            elif lowered in _ORDINALS:
                out.append(("ordinal", lowered))
            else:
                # dd / mm / yyyy / XX ...: a format hint or placeholder word,
                # never a date component.
                out.append(("placeholder", raw))
        else:
            out.append(("other", raw))
    return out


def residue_count(text):
    """Number of unfilled mask slots / placeholder words visible in ``text``."""
    return sum(len(value) if kind == "prompt" else 1
               for kind, value in _tokens(text)
               if kind in {"prompt", "placeholder"})


def _build(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _hint_layout(hint):
    """Order of day/month/year in a strptime-style or dd/mm/yyyy-style hint."""
    text = str(hint or "")
    if "%" in text:
        order = [m.group(1).lower() for m in re.finditer(r"%([dmY])", text)]
    else:
        order = [group[0].lower() for group in re.findall(r"[dDmMyY]+", text)]
        if any(len(g) > 2 and g[0] in "mM" and g.lower() != "mm" for g in re.findall(r"[dDmMyY]+", text)):
            return ""  # month NAME formats (MMM / MMMM) are not a numeric layout
    if len(order) == 3 and set(order) == {"d", "m", "y"}:
        return "".join(order)
    return ""


def date_candidates(text, format_hint=""):
    """Every calendar date ``text`` can represent, as ``[(layout, date)]``.

    ``layout`` is ``dmy`` / ``mdy`` / ``ymd`` / ``named``.  Empty when the text
    is not a complete, valid date (including any text that still contains
    mask placeholders).
    """
    toks = [t for t in _tokens(str(text or "").strip())
            if t[0] not in {"space", "weekday"} and t != ("sep", ",")]
    # Ordinal suffix words ("1st") are decoration.
    toks = [t for t in toks if t[0] != "ordinal"]
    if not toks or any(kind in {"prompt", "placeholder", "other"} for kind, _ in toks):
        return []
    has_space_sep = bool(re.search(r"\d\s+\d", str(text or "")))
    kinds = [kind for kind, _ in toks]
    nums = [v for k, v in toks if k == "num"]
    months = [v for k, v in toks if k == "month"]
    seps = [v for k, v in toks if k == "sep"]
    if ":" in seps:
        return []  # date-time values are out of scope: leave to literal comparison
    if len(months) == 1 and len(nums) == 2 and all(s in {"-", "/", "."} for s in seps):
        month = _MONTHS[months[0]]
        four = [n for n in nums if len(n) == 4]
        if len(four) != 1:
            return []
        year = int(four[0])
        day_text = nums[0] if nums[1] == four[0] else nums[1]
        if len(day_text) > 2:
            return []
        built = _build(year, month, int(day_text))
        return [("named", built)] if built else []
    if months or len(nums) != 3 or kinds.count("sep") not in {0, 2}:
        return []
    if len(set(seps)) > 1:
        return []
    if not seps and not has_space_sep:
        return []
    a, b, c = nums
    layouts = []
    if len(a) == 4 and len(b) <= 2 and len(c) <= 2:
        layouts.append(("ymd", int(a), int(b), int(c)))
    elif len(c) == 4 and len(a) <= 2 and len(b) <= 2:
        layouts.append(("dmy", int(c), int(b), int(a)))
        layouts.append(("mdy", int(c), int(a), int(b)))
    elif len(c) == 2 and len(a) <= 2 and len(b) <= 2:
        year = 2000 + int(c)
        layouts.append(("dmy", year, int(b), int(a)))
        layouts.append(("mdy", year, int(a), int(b)))
    found = []
    for layout, year, month, day in layouts:
        built = _build(year, month, day)
        if built:
            found.append((layout, built))
    wanted = _hint_layout(format_hint)
    if wanted:
        hinted = [item for item in found if item[0] == wanted]
        if hinted:
            return hinted
    return found


def is_date_shaped(text):
    """True when ``text`` *looks like* a date (valid or not).

    Used to decide whether date-aware entry/verification applies at all, so a
    deliberately invalid recorded date (``31/02/2026``) is still treated as a
    date entry and typed / checked faithfully rather than rejected.
    """
    value = str(text or "").strip()
    if not value or len(value) > 40:
        return False
    if date_candidates(value):
        return True
    toks = [t for t in _tokens(value) if t[0] not in {"space", "weekday", "ordinal"} and t != ("sep", ",")]
    if any(kind in {"prompt", "placeholder", "other"} for kind, _ in toks):
        return False
    nums = [v for k, v in toks if k == "num"]
    months = [v for k, v in toks if k == "month"]
    seps = {v for k, v in toks if k == "sep"}
    if ":" in seps or len(seps) > 1:
        return False
    if len(nums) == 3 and not months and len(seps) <= 1:
        sizes = sorted(len(n) for n in nums)
        return sizes[-1] in {2, 4} and sizes[0] <= 2 and (bool(seps) or bool(re.search(r"\d\s+\d", value)))
    if len(nums) == 2 and len(months) == 1:
        return True
    return False


def _normalise_literal(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _describe_slots(text):
    return ", ".join(sorted({value for kind, value in _tokens(text) if kind in {"prompt", "placeholder"}})) or "-"


def _head_before_first_slot(text):
    for match in _TOKEN.finditer(str(text or "")):
        raw = match.group(0)
        if raw[0] in _PROMPT_CHARS:
            return str(text)[:match.start()]
        if raw[0].isalpha() and not raw.isdigit():
            lowered = raw.lower()
            if lowered not in _MONTHS and lowered not in _WEEKDAYS and lowered not in _ORDINALS:
                return str(text)[:match.start()]
    return str(text or "")


def compare_date_values(expected, actual, format_hint=""):
    """Judge what a field holds against the date that was entered.

    Returns ``{verified, status, reason, expected, actual}`` where ``status`` is
    one of ``exact | match | literal | residue | incomplete | empty |
    not_a_date | mismatch``.
    """
    expected_text, actual_text = str(expected or ""), str(actual or "")
    result = {"expected": expected_text, "actual": actual_text}

    def done(verified, status, reason):
        result.update({"verified": verified, "status": status, "reason": reason})
        return result

    if expected_text == actual_text:
        return done(True, "exact", f"Field holds '{actual_text}' exactly as entered.")
    expected_cands = date_candidates(expected_text, format_hint)
    if not expected_cands:
        # The entered text is not a valid calendar date (a deliberate negative
        # test, or a non-date).  It must be held literally - never normalised.
        same = _normalise_literal(expected_text) == _normalise_literal(actual_text)
        if same:
            return done(True, "literal", f"Field holds the entered text '{actual_text}'.")
        if residue_count(actual_text):
            head = _head_before_first_slot(actual_text).rstrip(" /-.")
            if _normalise_literal(head) == _normalise_literal(expected_text):
                status = "residue"
            elif re.search(r"\d", actual_text):
                status = "incomplete"
            else:
                status = "empty"
            return done(False, status, (
                f"Expected '{expected_text}', but the field shows '{actual_text}' "
                f"with unfilled mask slots ({_describe_slots(actual_text)})."))
        return done(False, "mismatch", f"Expected '{expected_text}', found '{actual_text}'.")

    if not actual_text.strip():
        return done(False, "empty", f"Expected date '{expected_text}', but the field is empty.")
    slots = residue_count(actual_text)
    if slots:
        # Text before the first unfilled slot: is the whole date already there?
        head = _head_before_first_slot(actual_text).rstrip(" /-.")
        if head and set(c for _, c in date_candidates(head, format_hint)) & set(c for _, c in expected_cands):
            return done(False, "residue", (
                f"The field shows the date '{head}' followed by {slots} unfilled mask "
                f"slot(s) ({_describe_slots(actual_text)}): '{actual_text}'. The entry was "
                "not committed as a clean date value."))
        if re.search(r"\d", actual_text):
            return done(False, "incomplete", (
                f"The field shows an incomplete date '{actual_text}' ({slots} unfilled slot(s)); "
                f"expected '{expected_text}'."))
        if any(ch in _PROMPT_CHARS for ch in actual_text) or not re.search(r"[^\W\d_]{3,}", actual_text.replace("/", " ")) or all(
                kind != "placeholder" or set(value.lower()) <= set("dmyx") for kind, value in _tokens(actual_text)):
            return done(False, "empty", (
                f"The field shows only an empty input mask '{actual_text}'; expected '{expected_text}'."))
        return done(False, "not_a_date", (
            f"Expected date '{expected_text}', but the field holds '{actual_text}', which is not a date."))

    actual_cands = date_candidates(actual_text, format_hint)
    if not actual_cands:
        return done(False, "not_a_date", (
            f"Expected date '{expected_text}', but the field holds '{actual_text}', "
            "which is not a complete valid date."))
    expected_dates = {d for _, d in expected_cands}
    actual_dates = {d for _, d in actual_cands}
    if len(expected_cands) == 1 or len(actual_cands) == 1:
        matched = bool(expected_dates & actual_dates)
    else:
        # Both sides ambiguous (e.g. 01/10/2026 vs 10/01/2026): only the SAME
        # day/month ordering may be compared - never "any reading matches".
        matched = any(layout == other_layout and day == other_day
                      for layout, day in expected_cands for other_layout, other_day in actual_cands)
    if matched:
        return done(True, "match", (
            f"Field holds '{actual_text}', the same calendar date as the entered '{expected_text}'."))
    shown = sorted(d.isoformat() for d in actual_dates)
    wanted = sorted(d.isoformat() for d in expected_dates)
    return done(False, "mismatch", (
        f"Expected date '{expected_text}' ({' or '.join(wanted)}), but the field shows "
        f"'{actual_text}' ({' or '.join(shown)})."))


# ----------------------------------------------------------- browser helpers
_INSPECT_JS = r"""el => ({
  tag: el.tagName,
  type: (el.getAttribute('type') || '').toLowerCase(),
  read_only: !!el.readOnly,
  disabled: !!el.disabled,
  placeholder: el.getAttribute('placeholder') || '',
  format_hint: el.getAttribute('data-date-format') || el.getAttribute('data-format') ||
               el.getAttribute('data-mask') || '',
  value: typeof el.value === 'string' ? el.value : '',
  bad_input: !!(el.validity && el.validity.badInput)
})"""

_TEXT_LIKE = {"", "text", "tel", "search", "date"}


def inspect_input(node):
    """Facts about the live control (never its application identity)."""
    return node.evaluate(_INSPECT_JS)


def format_hint_of(info):
    """A usable date-format hint from the control itself, or ''."""
    for key in ("format_hint", "placeholder"):
        candidate = str((info or {}).get(key) or "")
        if _hint_layout(candidate):
            return candidate
    return ""


def entry_applicable(info, value):
    """Date-aware keyboard/native entry applies only to a date literal typed
    into an editable text-like or date input."""
    return bool(
        info and str(info.get("tag") or "").upper() == "INPUT"
        and str(info.get("type") or "") in _TEXT_LIKE
        and not info.get("read_only") and not info.get("disabled")
        and is_date_shaped(value)
    )


def _browser_order(page):
    try:
        return page.evaluate(
            "() => Intl.DateTimeFormat(navigator.language).formatToParts(new Date(2000, 10, 25))"
            ".filter(p => ['day','month','year'].includes(p.type)).map(p => p.type[0]).join('')"
        ) or ""
    except Exception:
        return ""


def to_native_iso(page, value, format_hint=""):
    candidates = date_candidates(value, format_hint)
    if not candidates:
        raise DateEntryError(
            f"'{value}' is not a valid calendar date, so it cannot be entered into a native date input.")
    if len(candidates) > 1:
        order = _browser_order(page)
        preferred = [c for c in candidates if c[0] == order]
        if len(preferred) != 1:
            raise DateEntryError(
                f"'{value}' could be read as more than one date and no format is declared; "
                "confirm the intended format.")
        candidates = preferred
    return candidates[0][1].isoformat()


def settled_value(node, first_read=None, interval_ms=80, max_ms=1000):
    """The field's value once it stops changing (mask/format handlers done)."""
    previous = node.input_value() if first_read is None else first_read
    deadline = time.monotonic() + max_ms / 1000.0
    while time.monotonic() < deadline:
        time.sleep(interval_ms / 1000.0)
        current = node.input_value()
        if current == previous:
            return current
        previous = current
    return previous


def _clear_and_home(page, node):
    node.focus()
    try:
        node.select_text()
    except Exception:
        page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.press("Home")


def enter_date_value(page, node, value, info=None, key_delay_ms=25):
    """Enter ``value`` the way the live control expects.  One field-local
    action; returns a report dict (never raises on a mismatch - the post-action
    verifier owns the verdict)."""
    info = info or inspect_input(node)
    hint = format_hint_of(info)
    report = {"typed": value, "format_hint": hint, "input_type": info.get("type") or "text"}
    if info.get("type") == "date":
        iso = to_native_iso(page, value, hint)
        node.fill(iso)
        report.update({"strategy": "native_date_fill", "entered": iso, "mask_detected": False,
                       "observed": node.input_value()})
        return report

    _clear_and_home(page, node)
    cleared = node.input_value()
    mask_detected = bool(residue_count(cleared))
    page.keyboard.type(str(value), delay=key_delay_ms)
    observed = settled_value(node)
    attempts = [{"strategy": "keyboard_typed", "observed": observed}]
    check = compare_date_values(value, observed, hint)
    digits_only = re.sub(r"\D", "", str(value))
    if (not check["verified"] and check["status"] in {"mismatch", "not_a_date", "incomplete", "empty"}
            and digits_only and digits_only != str(value) and date_candidates(value, hint)):
        # A mask that auto-inserts its own separators can reject typed ones.
        # Still ONE field-local edit sequence, decided by what the field now
        # holds (never a retry of an uncertain action).
        _clear_and_home(page, node)
        page.keyboard.type(digits_only, delay=key_delay_ms)
        observed = settled_value(node)
        attempts.append({"strategy": "keyboard_typed_digits_only", "observed": observed})
    report.update({
        "strategy": attempts[-1]["strategy"], "attempts": attempts, "observed": observed,
        "cleared_value": cleared, "mask_detected": mask_detected,
    })
    return report
