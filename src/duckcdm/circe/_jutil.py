"""Java·commons-lang3 동작 흉내: 출력이 circe-be(Java)와 글자 단위로 같아야 한다."""
import re
from decimal import ROUND_HALF_UP, Decimal


class CirceError(RuntimeError):
    """Java 쪽이 예외를 던지는 자리 (NPE, IllegalArgument, Jackson 오류 등)."""


def npe(what='NullPointerException'):
    raise CirceError(what)


def nn(x, what='value'):
    """Java 에서 null 역참조가 되는 자리."""
    if x is None:
        raise CirceError('NullPointerException: ' + what)
    return x


def i32(n):
    n &= 0xFFFFFFFF
    return n - 0x100000000 if n & 0x80000000 else n


def jstr(x):
    """String.valueOf / 문자열 연결 / %s·%d 의 결과."""
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
    """Double.toString (JDK 19+ 최단 자릿수 = 파이썬 repr 자릿수)."""
    if d != d:
        return 'NaN'
    if d in (float('inf'), float('-inf')):
        return 'Infinity' if d > 0 else '-Infinity'
    if d == 0:
        return '-0.0' if repr(d).startswith('-') else '0.0'
    sign, digs, exp = Decimal(repr(d)).as_tuple()
    digs = ''.join(map(str, digs)).rstrip('0') or '0'
    point = len(''.join(map(str, Decimal(repr(d)).as_tuple()[1]))) + exp     # 소수점 위치(앞자리 수)
    neg = '-' if sign else ''
    if 1e-3 <= abs(d) < 1e7:
        if point <= 0:
            return neg + '0.' + '0' * (-point) + digs
        if point >= len(digs):
            return neg + digs + '0' * (point - len(digs)) + '.0'
        return neg + digs[:point] + '.' + digs[point:]
    return neg + digs[0] + '.' + (digs[1:] or '0') + 'E' + str(point - 1)


def jjoin(items, sep):
    """commons-lang3 StringUtils.join: null 원소는 빈 문자열."""
    return sep.join('' if x is None else jstr(x) for x in items)


def jformat_f(d, prec):
    """String.format(Locale.US, "%.Nf", d) — Java 는 HALF_UP 반올림."""
    if d != d:
        return 'NaN'
    if d in (float('inf'), float('-inf')):
        return 'Infinity' if d > 0 else '-Infinity'
    q = Decimal(1).scaleb(-prec)
    return format(Decimal(repr(float(d))).quantize(q, rounding=ROUND_HALF_UP), 'f')


def java_replacement(rep):
    """Matcher.replaceAll 의 치환 문자열 해석(\\x, $n). 그룹이 없는 정규식이므로 $0 만 유효."""
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
    """commons-lang3 StringUtils.replaceAll(text, regex, replacement) — 여기서는 패턴이 정규식 특수문자 없는 문자열."""
    if text is None or literal_pattern is None or rep is None:
        return text
    parts = java_replacement(rep)

    def sub(m):
        return ''.join(v if k == 'lit' else m.group(0) for k, v in parts)
    return re.sub(literal_pattern, sub, text)


def jreplace(text, search, rep):
    """commons-lang3 StringUtils.replace: 문자 그대로 전부. null 인자는 원문 그대로."""
    if text is None or not search or rep is None:
        return text
    return text.replace(search, rep)


def is_empty(s):
    return s is None or s == ''


def commons_split(s, ch):
    """StringUtils.split(str, char): 빈 토큰 없이."""
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
