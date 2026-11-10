"""Evidence-based calendar actions shared by recorded scripts and replay.

No application selectors: ownership is supplied by recorder DOM evidence.
Unrecognized widgets/formats fail closed into interactive repair.
"""
from datetime import date, datetime, timezone
import re
import time


class CalendarError(ValueError):
    pass


class CalendarVerificationError(CalendarError):
    """A date click was dispatched; it must not be automatically repeated."""


_FORMATS = ('%Y-%m-%d', '%A, %B %d, %Y', '%B %d, %Y', '%d %B %Y',
            '%d %b %Y', '%m/%d/%Y', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d')


def date_format(value):
    value = str(value or '').strip()
    if '%' in value:
        return value
    for token, replacement in [('yyyy', '%Y'), ('YYYY', '%Y'), ('MMMM', '%B'),
                               ('MMM', '%b'), ('MM', '%m'), ('mm', '%m'), ('dd', '%d'), ('DD', '%d')]:
        value = value.replace(token, replacement)
    return value


def parse_date(value, format_hint=''):
    text = str(value or '').strip()
    formats = (date_format(format_hint),) if format_hint else _FORMATS
    matches = set()
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt).date()
            if '%A' in fmt and not re.search(r'\b' + re.escape(parsed.strftime('%A')) + r'\b', text, re.I):
                continue
            matches.add(parsed)
        except ValueError:
            continue
    if len(matches) != 1:
        raise CalendarError('Date is invalid, unsupported, or ambiguous; confirm its format.')
    return matches.pop()


def infer_format(observed, target):
    matches = []
    for fmt in _FORMATS:
        try:
            if datetime.strptime(observed, fmt).date() == target:
                matches.append(fmt)
        except ValueError:
            pass
    if not matches:
        raise CalendarError('Recorded input value does not represent the selected date.')
    return matches[0]


def locator(scope, descriptor):
    strategy = descriptor.get('strategy')
    value = descriptor.get('value', '')
    if strategy == 'id':
        # Attribute equality avoids CSS identifier escaping/generated-ID assumptions.
        return scope.locator('[id=' + repr(value) + ']')
    if strategy in {'name', 'testid'}:
        attr = 'name' if strategy == 'name' else 'data-testid'
        return scope.locator('[' + attr + '=' + repr(value) + ']')
    if strategy == 'role':
        return scope.get_by_role(descriptor['role'], name=descriptor['name'], exact=True)
    if strategy in {'label', 'title', 'placeholder', 'text'}:
        return getattr(scope, 'get_by_' + strategy)(value, exact=True)
    if strategy in {'css', 'xpath'}:
        return scope.locator(('xpath=' if strategy == 'xpath' else '') + value)
    raise CalendarError('Unsupported recorded calendar locator.')


def resolve_unique(scope, descriptors, label, visible=True):
    for descriptor in descriptors or []:
        candidate = locator(scope, descriptor)
        if candidate.count() == 1 and (not visible or candidate.is_visible()):
            return candidate
    raise CalendarError(label + ' is absent or ambiguous; confirm the intended calendar.')


def displayed_month(popup):
    values = popup.evaluate(r'''el => {
      const out = [];
      if (el.dataset.calendarYear && el.dataset.calendarMonth)
        out.push(el.dataset.calendarYear + '-' + el.dataset.calendarMonth);
      for (const n of Array.from(el.querySelectorAll('[data-calendar-heading],[aria-live],.ui-datepicker-title,.flatpickr-current-month,.k-calendar-title,.ajax__calendar_title,.datepicker-switch,.react-datepicker__current-month,.rdp-caption_label,.MuiPickersCalendarHeader-label,table caption')).slice(0, 20)) {
        if (n.getClientRects().length) {
          const month=n.querySelector('.flatpickr-monthDropdown-months,.ui-datepicker-month');
          const year=n.querySelector('.cur-year,.ui-datepicker-year');
          const monthName=month && month.selectedOptions ? month.selectedOptions[0].textContent : ((n.querySelector('.cur-month') || {}).textContent || '');
          const combined=monthName && year ? monthName+' '+year.value : (n.innerText || n.textContent || '');
          out.push(combined.slice(0,100));
        }
      }
      return out;
    }''')
    months = set()
    for value in values:
        value = re.sub(r'\s+', ' ', value.replace(',', ' ')).strip()
        match = re.fullmatch(r'\s*(\d{4})-(\d{1,2})\s*', value)
        if match and 1 <= int(match[2]) <= 12:
            months.add((int(match[1]), int(match[2])))
        for fmt in ('%B %Y', '%b %Y', '%Y %B'):
            try:
                parsed = datetime.strptime(value.strip(), fmt)
                months.add((parsed.year, parsed.month))
            except ValueError:
                pass
    if len(months) != 1:
        raise CalendarError('Displayed calendar month/year is unknown or ambiguous.')
    return months.pop()


def _navigation_candidates(popup, direction):
    """Ordered, generic month-navigation candidates (no widget-specific selectors).

    Every candidate must still resolve to EXACTLY one visible, enabled control,
    and navigate_month() verifies the displayed month actually moved by one.
    """
    forward = direction > 0
    word = 'Next' if forward else 'Previous'
    short = 'Next' if forward else r'Prev(?:ious)?'
    glyphs = r'[›»>→▶❯]' if forward else r'[‹«<←◀❮]'
    yield popup.get_by_role('button', name=re.compile(r'^' + word + r'(?: month)?$', re.I))
    yield popup.get_by_title(word + ' month', exact=True)
    yield popup.get_by_role('button', name=re.compile(r'^(?:' + short + r')(?: (?:month|page))?$|^' + glyphs + r'$', re.I))
    yield popup.get_by_role('button', name=re.compile(r'(?:' + short + r').{0,12}month', re.I))
    yield popup.get_by_text(re.compile(r'^\s*' + glyphs + r'\s*$'))
    yield popup.locator('[data-action="' + ('next' if forward else 'prev') + '"],[data-calendar-nav="' + ('next' if forward else 'prev') + '"]')
    yield popup.locator('[aria-label*="' + ('next' if forward else 'previous') + ' month" i],[title*="' + ('next' if forward else 'previous') + ' month" i]')
    # Class conventions (jQuery UI, flatpickr, bootstrap-datepicker, react-datepicker ...).
    token = 'next' if forward else 'prev'
    yield popup.locator('[class*="' + token + '" i]:not([class*="year" i]):not([class*="decade" i]):not([class*="disabled" i])')


def navigate_month(popup, direction):
    candidate = None
    for option in _navigation_candidates(popup, direction):
        try:
            usable = option.count() == 1 and option.is_visible() and option.is_enabled()
        except Exception:
            usable = False
        if usable:
            candidate = option
            break
    if candidate is None:
        raise CalendarError('Unique supported month navigation is unavailable; confirm the target date.')
    previous = displayed_month(popup)
    candidate.click(trial=True, timeout=1500)
    candidate.click(timeout=1500)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        current = displayed_month(popup)
        if current != previous:
            expected = date(previous[0] + (previous[1] == 12 and direction > 0) - (previous[1] == 1 and direction < 0),
                            (previous[1] - 1 + direction) % 12 + 1, 1)
            if current != (expected.year, expected.month):
                raise CalendarError('Calendar navigation changed to an unexpected month.')
            return
        popup.page.wait_for_timeout(50)
    raise CalendarError('Calendar month did not change after navigation.')


_NOT_SELECTABLE = re.compile(r'\b(?:not available|unavailable|disabled|outside month)\b', re.I)


def _identity_date(attr, value, format_hint=''):
    """Date represented by one cell identity, or None. Strict: only an exact full date."""
    text = str(value or '').strip()
    if attr == 'data-date' and re.fullmatch(r'\d{12,13}', text):
        # bootstrap-datepicker style: UTC-midnight epoch milliseconds.
        return datetime.fromtimestamp(int(text) / (1000 if len(text) == 13 else 1), timezone.utc).date()
    candidates = [text]
    cleaned = re.sub(r'^(?:choose|select|selected|today,?)\s+', '', text, flags=re.I)
    cleaned = re.sub(r'(?<=\d)(?:st|nd|rd|th)\b', '', cleaned, flags=re.I)
    if cleaned != text:
        if _NOT_SELECTABLE.search(text):
            return None
        candidates.append(cleaned)
    for candidate in candidates:
        try:
            return parse_date(candidate, format_hint if re.fullmatch(r'[\d/-]+', candidate) and not re.match(r'^\d{4}-', candidate) else '')
        except CalendarError:
            continue
    return None


def resolve_date(popup, target, format_hint=''):
    # Capture at most 250 bounded date identities, never full HTML or day IDs.
    identities = popup.evaluate(r'''el => Array.from(el.querySelectorAll('[title],[aria-label],[data-date],[datetime]')).slice(0,250).flatMap(n => {
      if (!n.getClientRects().length || n.disabled || n.getAttribute('aria-disabled') === 'true') return [];
      if (n.closest('.ui-state-disabled,.flatpickr-disabled,.k-disabled,.k-state-disabled')) return [];
      let actionable=n.closest('button,a[href],[role="gridcell"],[role="button"],[onclick],[tabindex]:not([tabindex="-1"])');
      let cursor=n;
      for(let depth=0; !actionable && cursor && depth<4;depth++,cursor=cursor.parentElement) {
        if(getComputedStyle(cursor).cursor==='pointer') actionable=cursor;
      }
      if(!actionable || actionable.disabled || actionable.getAttribute('aria-disabled')==='true') return [];
      return ['title','aria-label','data-date','datetime'].map(attr => ({attr, value:n.getAttribute(attr)})).filter(x => x.value && x.value.length <= 120);
    })''')
    selectors = []
    for identity in identities:
        full_date = _identity_date(identity['attr'], identity['value'], format_hint)
        if full_date == target:
            selector = '[' + identity['attr'] + '=' + repr(identity['value']) + ']'
            if selector not in selectors:
                selectors.append(selector)
    matches = []
    for selector in selectors:
        state_filter = ':visible:not([disabled]):not([aria-disabled="true"]):not([aria-disabled="true"] *)'
        for disabled_class in ('ui-state-disabled','flatpickr-disabled','k-disabled','k-state-disabled'):
            state_filter += ':not(.' + disabled_class + '):not(.' + disabled_class + ' *)'
        candidate = popup.locator(selector + state_filter)
        if candidate.count() != 1:
            raise CalendarError('Target date matches multiple cells; confirmation is required.')
        if candidate.is_visible() and candidate.is_enabled():
            if not any(candidate.evaluate('(el, other) => el === other', old.element_handle()) for old in matches):
                matches.append(candidate)
    if len(matches) > 1:
        raise CalendarError('Multiple actionable cells represent the date; confirmation is required.')
    return matches[0] if matches else None


class CalendarAction:
    """One verified selection; callable from a clean persisted Python script."""
    def __init__(self, page, metadata):
        self.page, self.metadata = page, metadata
        self.dispatched = False

    def click(self):
        try:
            return self._click()
        except CalendarError:
            raise
        except Exception as ex:
            error = CalendarVerificationError if self.dispatched else CalendarError
            raise error('Calendar interaction could not be verified (' + type(ex).__name__ + '); confirmation is required.') from ex

    def _click(self):
        data = self.metadata
        target = parse_date(data['target'], data.get('target_format', ''))
        owner = resolve_unique(self.page, data.get('input'), 'Owning date input')
        if owner.evaluate("el => !['INPUT','TEXTAREA'].includes(el.tagName)"):
            raise CalendarError('Recorded calendar owner is not a date input.')
        if owner.evaluate("el => !!(el._flatpickr && el._flatpickr.config && el._flatpickr.config.mode !== 'single')"):
            raise CalendarError('Range and multi-date calendars require explicit confirmation and are not supported automatically.')
        fmt = data.get('format') or ('%Y-%m-%d' if owner.get_attribute('type') == 'date' else '')
        if not fmt and data.get('selected_value'):
            fmt = infer_format(data['selected_value'], target)
        if owner.get_attribute('type') == 'date':
            if not owner.is_enabled() or not owner.is_editable():
                raise CalendarError('HTML date input is not editable.')
            for attr in ('min', 'max'):
                limit = owner.get_attribute(attr)
                if limit and ((attr == 'min' and target < parse_date(limit)) or (attr == 'max' and target > parse_date(limit))):
                    raise CalendarError('Target date is outside the input bounds.')
            self.dispatched = True
            owner.fill(target.isoformat())
        else:
            opener = resolve_unique(self.page, data.get('opener'), 'Calendar opener', visible=False) if data.get('opener') else None
            popup_descriptors = data.get('popup') or []
            controlled = ''
            if opener is not None:
                controlled = opener.get_attribute('aria-controls') or opener.get_attribute('aria-owns') or ''
                if controlled:
                    ids = controlled.split()
                    if len(ids) != 1:
                        raise CalendarError('Opener owns multiple popups; confirmation is required.')
                    popup_descriptors = [{'strategy':'id','value':ids[0]}]
            popup = resolve_unique(self.page, popup_descriptors, 'Owned calendar', visible=False)
            relation_node = opener if opener is not None else popup
            owns_input = relation_node.evaluate(r'''(el, nodes) => {
              const input=nodes.input;
              if (el === input) return true;
              const explicit = el.getAttribute('data-calendar-input');
              if (explicit) return document.getElementById(explicit) === input;
              let scope = el.parentElement;
              for (let depth=0; scope && depth<4; depth++,scope=scope.parentElement) {
                const inputs=Array.from(scope.querySelectorAll('input,textarea')).filter(n => n.getClientRects().length && !nodes.popup.contains(n) && !/^(hidden|button|submit|reset|image|checkbox|radio)$/.test(n.type || ''));
                if (inputs.length===1) return inputs[0]===input;
                if (inputs.length>1) return false;
              }
              return false;
            }''', {'input':owner.element_handle(), 'popup':popup.element_handle()})
            if not owns_input:
                raise CalendarError('Live calendar/input ownership is ambiguous or changed; confirmation is required.')
            if opener is not None and not controlled:
                popup_owned = popup.evaluate(r'''(el, nodes) => {
                  const explicit=el.getAttribute('data-calendar-input');
                  if(explicit) return document.getElementById(explicit)===nodes.input;
                  const labelled=(el.getAttribute('aria-labelledby') || '').split(/\s+/);
                  if(nodes.opener.id && labelled.includes(nodes.opener.id)) return true;
                  let scope=nodes.opener.parentElement;
                  for(let depth=0;scope && depth<4;depth++,scope=scope.parentElement) {
                    if(!scope.contains(el)) continue;
                    const inputs=Array.from(scope.querySelectorAll('input,textarea')).filter(n => n.getClientRects().length && !el.contains(n) && !/^(hidden|button|submit|reset|image|checkbox|radio)$/.test(n.type || ''));
                    return inputs.length===1 && inputs[0]===nodes.input;
                  }
                  return false;
                }''', {'input':owner.element_handle(), 'opener':opener.element_handle()})
                if not popup_owned:
                    raise CalendarError('Popup ownership is not proven; confirmation is required.')
            if not popup.is_visible():
                if opener is None:
                    raise CalendarError('Inline calendar is hidden and has no proven opener.')
                opener.click(trial=True, timeout=1500)
                opener.click(timeout=1500)
                popup.wait_for(state='visible', timeout=3000)
            candidate = resolve_date(popup, target, fmt)
            if candidate is None:
                wanted = (target.year, target.month)
                for _ in range(24):
                    current = displayed_month(popup)
                    if current == wanted:
                        break
                    navigate_month(popup, 1 if current < wanted else -1)
                if displayed_month(popup) != wanted:
                    raise CalendarError('Target month exceeds the 24-month navigation limit.')
                candidate = resolve_date(popup, target, fmt)
            if candidate is None:
                raise CalendarError('Exact enabled target date is unavailable; confirmation is required.')
            candidate.click(trial=True, timeout=1500)
            self.dispatched = True
            candidate.click(timeout=1500)
        verify_selection(self.page, data)


def verify_selection(page, data):
    target = parse_date(data['target'], data.get('target_format', ''))
    owner = resolve_unique(page, data.get('input'), 'Owning date input')
    fmt = data.get('format') or ('%Y-%m-%d' if owner.get_attribute('type') == 'date' else '')
    if not fmt and data.get('selected_value'):
        fmt = infer_format(data['selected_value'], target)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            if parse_date(owner.input_value(), fmt) == target and owner.evaluate('el => !el.checkValidity || el.checkValidity()'):
                return
        except CalendarError:
            pass
        page.wait_for_timeout(50)
    raise CalendarVerificationError('Selected date was not confirmed in its owning input; the action was not repeated.')
