"""Emulation of Java/commons-lang3 behaviour: output must match circe-be (Java) character for character."""
import re
from decimal import ROUND_HALF_UP, Decimal


class CirceError(RuntimeError):
    """Where the Java side throws (NPE, IllegalArgument, Jackson errors, etc.)."""


def npe(what='NullPointerException'):
    raise CirceError(what)


def nn(x, what='value'):
    """Where Java would dereference null."""
    if x is None:
        raise CirceError('NullPointerException: ' + what)
    return x


def i32(n):
    n &= 0xFFFFFFFF
    return n - 0x100000000 if n & 0x80000000 else n


def jstr(x):
    """Result of String.valueOf / string concatenation / %s, %d."""
    if x is None:
        return 'null'
    if x is True:
        return 'true'
    if x is False:
        return 'false'
    if isinstance(x, float):
        return java_double_str(x)
    return str(x)


def java_double_str(d):
    """Double.toString (JDK 19+ shortest digits = Python repr digits)."""
    if d != d:
        return 'NaN'
    if d in (float('inf'), float('-inf')):
        return 'Infinity' if d > 0 else '-Infinity'
    if d == 0:
        return '-0.0' if repr(d).startswith('-') else '0.0'
    sign, digs, exp = Decimal(repr(d)).as_tuple()
    digs = ''.join(map(str, digs)).rstrip('0') or '0'
    point = len(''.join(map(str, Decimal(repr(d)).as_tuple()[1]))) + exp     # decimal point position (number of integer digits)
    neg = '-' if sign else ''
    if 1e-3 <= abs(d) < 1e7:
        if point <= 0:
            return neg + '0.' + '0' * (-point) + digs
        if point >= len(digs):
            return neg + digs + '0' * (point - len(digs)) + '.0'
        return neg + digs[:point] + '.' + digs[point:]
    return neg + digs[0] + '.' + (digs[1:] or '0') + 'E' + str(point - 1)


def jjoin(items, sep):
    """commons-lang3 StringUtils.join: null elements become empty strings."""
    return sep.join('' if x is None else jstr(x) for x in items)


def jformat_f(d, prec):
    """String.format(Locale.US, "%.Nf", d) — Java rounds HALF_UP."""
    if d != d:
        return 'NaN'
    if d in (float('inf'), float('-inf')):
        return 'Infinity' if d > 0 else '-Infinity'
    q = Decimal(1).scaleb(-prec)
    return format(Decimal(repr(float(d))).quantize(q, rounding=ROUND_HALF_UP), 'f')


def java_replacement(rep):
    """Interpret a Matcher.replaceAll replacement string (\\x, $n). The regex has no groups, so only $0 is valid."""
    out = []
    i = 0
    while i < len(rep):
        c = rep[i]
        if c == '\\':
            i += 1
            if i >= len(rep):
                raise CirceError('IllegalArgumentException: character to be escaped is missing')
            out.append(('lit', rep[i]))
        elif c == '$':
            i += 1
            if i >= len(rep):
                raise CirceError('IllegalArgumentException: Illegal group reference: group index is missing')
            if rep[i] == '{':
                raise CirceError('IllegalArgumentException: No group with name')
            if not rep[i].isdigit():
                raise CirceError('IllegalArgumentException: Illegal group reference')
            g = int(rep[i])
            if g != 0:
                raise CirceError('IndexOutOfBoundsException: No group ' + str(g))
            out.append(('grp', 0))
        else:
            out.append(('lit', c))
        i += 1
    return out


def regex_replace_all(text, literal_pattern, rep):
    """commons-lang3 StringUtils.replaceAll(text, regex, replacement) — here the pattern is a string with no regex metacharacters."""
    if text is None or literal_pattern is None or rep is None:
        return text
    parts = java_replacement(rep)

    def sub(m):
        return ''.join(v if k == 'lit' else m.group(0) for k, v in parts)
    return re.sub(literal_pattern, sub, text)


def jreplace(text, search, rep):
    """commons-lang3 StringUtils.replace: literal, all occurrences. A null argument returns the text unchanged."""
    if text is None or not search or rep is None:
        return text
    return text.replace(search, rep)


def is_empty(s):
    return s is None or s == ''


def commons_split(s, ch):
    """StringUtils.split(str, char): no empty tokens."""
    return [p for p in s.split(ch) if p != ''] if s is not None else None


_INT_RE = re.compile(r'[+-]?\d+\Z')


def java_parse_int(s):
    """Integer.valueOf(String)."""
    if s is None or not _INT_RE.match(s):
        raise CirceError('NumberFormatException: For input string: "%s"' % s)
    v = int(s)
    if not -2**31 <= v < 2**31:
        raise CirceError('NumberFormatException: For input string: "%s"' % s)
    return v
