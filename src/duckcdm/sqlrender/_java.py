"""Helpers that emulate Java behaviour exactly.

Output must match SqlRender (Java) character for character, so places where Python defaults differ are aligned here:
String.trim/split, Character.isWhitespace/isLetterOrDigit, HashMap iteration order, exceptions on out-of-range substring.
"""
import re
import unicodedata

__all__ = ['JavaError', 'trim', 'split', 'is_whitespace', 'is_letter_or_digit', 'is_letter', 'substring',
           'char_at', 'hash_order', 'strip_trailing_ws_once',
           'strip_ws_before_end', 'remove_blank_lines']


class JavaError(RuntimeError):
    """Where the Java side throws (StringIndexOutOfBounds, RuntimeException, etc.)."""


def trim(s):
    """String.trim(): strips leading/trailing code points <= U+0020 (Unicode whitespace is left alone)."""
    i, j = 0, len(s)
    while i < j and s[i] <= ' ':
        i += 1
    while j > i and s[j - 1] <= ' ':
        j -= 1
    return s[i:j]


def split(s, regex):
    """String.split(regex): drops trailing empty strings; [s] if there is no match at all."""
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
    """Character.isLetterOrDigit: a letter or Nd (decimal digit)."""
    c = unicodedata.category(ch)
    return c in ('Lu', 'Ll', 'Lt', 'Lm', 'Lo', 'Nd')


def substring(s, start, end=None):
    """String.substring: throws when out of range."""
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
    """java.lang.String.hashCode (over UTF-16 units)."""
    h = 0
    data = s.encode('utf-16-be')
    for k in range(0, len(data), 2):
        h = (31 * h + ((data[k] << 8) | data[k + 1])) & 0xFFFFFFFF
    return h


def hash_order(keys):
    """Order keys in java.util.HashMap iteration order (takes a list in insertion order).
    Capacity starts at 16 and doubles when size exceeds 0.75x. Bucket = (h ^ h>>>16) & (cap-1); within a bucket, insertion order."""
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
    """s.replaceAll("\\s$", "") — in Java \\s is [ \\t\\n\\x0B\\f\\r] and $ is the end or just before a final line break."""
    return re.sub(r'[ \t\n\x0b\f\r]$', '', s)


_LINE_TERMS = '\n\r\u0085  '


def strip_ws_before_end(s):
    """s.replaceAll("\\\\s$", "") with Java rules exactly: $ is the end of input or just before a final line terminator (including \\r\\n)."""
    n = len(s)
    ends = {n}
    if s.endswith('\r\n'):
        ends.add(n - 2)
    elif n and s[-1] in _LINE_TERMS:
        ends.add(n - 1)
    drop = {p - 1 for p in ends if p > 0 and s[p - 1] in ' \t\n\x0b\f\r'}
    return ''.join(ch for i, ch in enumerate(s) if i not in drop)


# Java (?m)^[ \t]*\r?\n : ^ is the start, after [\n\u0085  ], or after a \r not followed by \n
_BLANK_LINE_RE = re.compile('(?:\\A|(?<=[\n\u0085  ])|(?<=\r)(?!\n))[ \t]*\r?\n')


def remove_blank_lines(s):
    return _BLANK_LINE_RE.sub('', s)
