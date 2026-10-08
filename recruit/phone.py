import re

_DIGITS = re.compile(r"\D+")


def normalize_phone(raw):
    """Return an E.164 phone number (+9725…) or None if it can't be understood.

    Accepts Israeli local format (05x-xxxxxxx), 972… without plus, and any
    international number that already starts with +.
    """
    if raw is None:
        return None
    raw = str(raw).strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw[len("whatsapp:"):]
    has_plus = raw.startswith("+")
    digits = _DIGITS.sub("", raw)
    if not digits:
        return None
    if has_plus:
        number = "+" + digits
    elif digits.startswith("00"):
        number = "+" + digits[2:]
    elif digits.startswith("972"):
        number = "+" + digits
    elif digits.startswith("0") and len(digits) in (9, 10):
        number = "+972" + digits[1:]
    elif len(digits) == 9 and digits.startswith("5"):
        number = "+972" + digits
    else:
        return None
    if not 8 <= len(number) - 1 <= 15:
        return None
    return number
