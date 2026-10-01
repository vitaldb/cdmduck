"""Java 동작을 그대로 흉내 내는 보조 함수들.

SqlRender(Java) 와 출력이 글자 단위로 같아야 하므로 Python 기본 동작과 다른 부분을 여기서 맞춘다:
String.trim/split, Character.isWhitespace/isLetterOrDigit, HashMap 순회 순서, 범위를 벗어난 substring 의 예외.
"""
import re
import unicodedata

__all__ = ['JavaError', 'trim', 'split', 'is_whitespace', 'is_letter_or_digit', 'is_letter', 'substring',
           'char_at', 'hash_order', 'strip_trailing_ws_once',
           'strip_ws_before_end', 'remove_blank_lines']


class JavaError(RuntimeError):
    """Java 쪽이 예외(StringIndexOutOfBounds, RuntimeException 등)를 던지는 자리."""


def trim(s):
    """String.trim(): 앞뒤의 코드포인트 ≤ U+0020 문자를 지운다(유니코드 공백은 건드리지 않음)."""
    i, j = 0, len(s)
    while i < j and s[i] <= ' ':
        i += 1
    while j > i and s[j - 1] <= ' ':
        j -= 1
    return s[i:j]


def split(s, regex):
    """String.split(regex): 끝쪽 빈 문자열을 버리고, 일치가 전혀 없으면 [s]."""
    parts = re.split(regex, s)
    if len(parts) == 1:
        return [s]
    while parts and parts[-1] == '':
        parts.pop()
    return parts


_JAVA_SPACE_EXCLUDED = {' ', ' ', ' '}


def is_whitespace(ch):
    """Character.isWhitespace."""
    if ch in '\t\n\u000b\f\r\u001c\u001d\u001e\u001f':
        return True
    if ch in _JAVA_SPACE_EXCLUDED:
        return False
    return unicodedata.category(ch) in ('Zs', 'Zl', 'Zp')


def is_letter(ch):
    """Character.isLetter: Lu Ll Lt Lm Lo."""
    return unicodedata.category(ch) in ('Lu', 'Ll', 'Lt', 'Lm', 'Lo')


def is_letter_or_digit(ch):
    """Character.isLetterOrDigit: 문자 또는 Nd(십진 숫자)."""
    c = unicodedata.category(ch)
    return c in ('Lu', 'Ll', 'Lt', 'Lm', 'Lo', 'Nd')


def substring(s, start, end=None):
    """String.substring: 범위를 벗어나면 예외."""
    if end is None:
        end = len(s)
    if start < 0 or end > len(s) or start > end:
        raise JavaError(f'StringIndexOutOfBoundsException: begin {start}, end {end}, length {len(s)}')
    return s[start:end]


def char_at(s, i):
    if i < 0 or i >= len(s):
        raise JavaError(f'StringIndexOutOfBoundsException: index {i}, length {len(s)}')
    return s[i]


def _string_hash(s):
    """java.lang.String.hashCode (UTF-16 단위)."""
    h = 0
    data = s.encode('utf-16-be')
    for k in range(0, len(data), 2):
        h = (31 * h + ((data[k] << 8) | data[k + 1])) & 0xFFFFFFFF
    return h


def hash_order(keys):
    """java.util.HashMap 의 순회 순서로 키를 정렬한다(삽입 순서 목록을 받는다).
    용량은 16에서 시작해 크기가 0.75배를 넘으면 두 배. 버킷 = (h ^ h>>>16) & (cap-1), 같은 버킷은 삽입 순서."""
    n = len(keys)
    cap = 16
    while n > cap * 0.75:
        cap *= 2
    def bucket(k):
        h = _string_hash(k)
        h ^= h >> 16
        return h & (cap - 1)
    return [k for _, _, k in sorted((bucket(k), i, k) for i, k in enumerate(keys))]


def strip_trailing_ws_once(s):
    """s.replaceAll("\\s$", "") — Java 의 \\s 는 [ \\t\\n\\x0B\\f\\r], $ 는 끝 또는 마지막 줄바꿈 앞."""
    return re.sub(r'[ \t\n\x0b\f\r]$', '', s)


_LINE_TERMS = '\n\r\u0085  '


def strip_ws_before_end(s):
    """s.replaceAll("\\\\s$", "") 를 Java 규칙 그대로: $ 는 입력 끝 또는 마지막 줄끝문자(\\r\\n 포함) 바로 앞."""
    n = len(s)
    ends = {n}
    if s.endswith('\r\n'):
        ends.add(n - 2)
    elif n and s[-1] in _LINE_TERMS:
        ends.add(n - 1)
    drop = {p - 1 for p in ends if p > 0 and s[p - 1] in ' \t\n\x0b\f\r'}
    return ''.join(ch for i, ch in enumerate(s) if i not in drop)


# Java (?m)^[ \t]*\r?\n : ^ 는 처음, [\n\u0085  ] 뒤, 또는 \n 이 따르지 않는 \r 뒤
_BLANK_LINE_RE = re.compile('(?:\\A|(?<=[\n\u0085  ])|(?<=\r)(?!\n))[ \t]*\r?\n')


def remove_blank_lines(s):
    return _BLANK_LINE_RE.sub('', s)
