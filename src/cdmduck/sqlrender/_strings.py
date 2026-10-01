"""org.ohdsi.sql.StringUtils · SqlSplit 포팅."""
import re
from bisect import bisect_left, bisect_right
from collections import OrderedDict

from ._java import JavaError, char_at, is_letter_or_digit, is_whitespace, substring

# Java 의 '.' 는 모든 줄바꿈 문자(\n \r \u0085 \u2028 \u2029)를 제외한다
REGEX_ESCAPED_APOSTROPHES = r"""(['"])((?!\1)[^\n\r\u0085\u2028\u2029]|\1{2})*\1"""
HINT_KEY_WORD = 'hint'


class Token:
    __slots__ = ('start', 'end', 'text', 'in_quotes')

    def __init__(self, other=None):
        if other is None:
            self.start = self.end = 0
            self.text = ''
            self.in_quotes = False
        else:
            self.start, self.end, self.text = other.start, other.end, other.text
            self.in_quotes = False          # Java 복사 생성자는 inQuotes 를 복사하지 않는다

    def is_identifier(self):
        return all(is_letter_or_digit(ch) or ch == '_' for ch in self.text)

    def __repr__(self):
        return f'Token({self.text!r}@{self.start}:{self.end}{" q" if self.in_quotes else ""})'


def replace(string, start, end, replacement):
    if end > len(string):
        return substring(string, 0, start) + replacement
    return substring(string, 0, start) + replacement + substring(string, end)


def replace_all(result, search, repl):
    """StringUtils.replaceAll: 정규식이 아닌 문자 그대로, 바꾼 뒤 위치부터 계속."""
    pos = 0
    while True:
        pos = result.find(search, pos)
        if pos == -1:
            return result
        result = replace(result, pos, pos + len(search), repl)
        pos += len(repl)


def _char_class(pred, extra=''):
    """BMP 전체에서 pred 가 참인 글자들을 정규식 문자 클래스로 (Java 는 UTF-16 단위로 판정)."""
    ranges = []
    lo = None
    for c in range(0x10000):
        ok = (0xD800 <= c <= 0xDFFF) is False and (pred(chr(c)) or chr(c) in extra)
        if ok and lo is None:
            lo = c
        elif not ok and lo is not None:
            ranges.append((lo, c - 1))
            lo = None
    if lo is not None:
        ranges.append((lo, 0xFFFF))
    esc = lambda c: '\\u%04x' % c
    return ''.join(esc(a) if a == b else esc(a) + '-' + esc(b) for a, b in ranges)


_WORD = _char_class(is_letter_or_digit, '_@')
_WS = _char_class(is_whitespace)
_TOKEN_RE = re.compile(f'([{_WORD}]+)|[{_WS}]+|(.)', re.S)


def _new_token(start, end, text, in_quotes):
    t = Token()
    t.start, t.end, t.text, t.in_quotes = start, end, text, in_quotes
    return t


class Tokens:
    """토큰 목록을 평행 리스트로 (search 가 빠르게 훑도록). state 는 토큰 직전의 따옴표 상태(1=작은, 2=큰)."""
    __slots__ = ('sql', 'texts', 'starts', 'ends', 'states')

    def __init__(self, sql, texts, starts, ends, states):
        self.sql, self.texts, self.starts, self.ends, self.states = sql, texts, starts, ends, states

    def __len__(self):
        return len(self.texts)

    def token(self, i):
        return _new_token(self.starts[i], self.ends[i], self.texts[i], self.states[i] != 0)

    def to_list(self):
        return [self.token(i) for i in range(len(self.texts))]


def _scan(sql, pos, state, texts, starts, ends, states, resync=None):
    """pos 부터 state(따옴표 상태)로 훑어 리스트에 덧붙인다. resync(x, state) 가 참을 돌려주면 x 에서 멈추고 True."""
    in_single, in_double = bool(state & 1), bool(state & 2)
    n = len(sql)
    match = _TOKEN_RE.match
    while pos < n:
        m = match(sql, pos)
        word = m.group(1)
        st = in_single | (in_double << 1)
        if word is not None:
            if resync is not None and resync(pos, st):
                return True
            texts.append(word); starts.append(pos); ends.append(m.end()); states.append(st)
            pos = m.end()
            continue
        ch = m.group(2)
        if ch is None:                      # 공백
            pos = m.end()
            continue
        # Java 는 마지막 글자가 '-' 나 '/' 이면 charAt(cursor+1) 에서 예외를 던진다
        if ch == '-' and char_at(sql, pos + 1) == '-' and not in_single and not in_double \
                and (n - pos < 6 or sql[pos + 2:pos + 6] != HINT_KEY_WORD):
            e = sql.find('\n', pos + 1)
            if e == -1:
                break
            pos = e + 1
            continue
        if ch == '/' and char_at(sql, pos + 1) == '*' and not in_single and not in_double:
            e = sql.find('*/', pos + 1)
            if e == -1:
                break
            pos = e + 2
            continue
        if resync is not None and resync(pos, st):
            return True
        texts.append(ch); starts.append(pos); ends.append(pos + 1); states.append(st)
        if ch == "'" and not in_double:
            in_single = not in_single
        if ch == '"' and not in_single:
            in_double = not in_double
        pos += 1
    return False


def _common_prefix(a, b):
    lo, hi = 0, min(len(a), len(b))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _common_suffix(a, b, limit):
    lo, hi = 0, limit
    la, lb = len(a), len(b)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[la - mid:] == b[lb - mid:]:
            lo = mid
        else:
            hi = mid - 1
    return lo


_LOOKAHEAD = 6          # 토크나이저가 앞을 내다보는 최대 글자 수('--hint' 판정)
_recent = OrderedDict()
_last_big = None          # 증분 토큰화의 기준: 가장 최근에 토큰화한 긴 문자열


def _tokenize_full(sql):
    texts, starts, ends, states = [], [], [], []
    _scan(sql, 0, 0, texts, starts, ends, states)
    return Tokens(sql, texts, starts, ends, states)


def _tokenize_incremental(sql, old):
    """old(최근에 토큰화한 문자열)과 공통 앞·뒤를 재사용하고 바뀐 부분만 다시 훑는다."""
    o = old.sql
    cp = _common_prefix(o, sql)
    cs = _common_suffix(o, sql, min(len(o), len(sql)) - cp)
    # 재개 지점: 시작+내다보기가 공통 앞부분 안에 드는 마지막 토큰 → 그 앞의 모든 판정이 두 문자열에서 같다
    k = bisect_right(old.starts, cp - _LOOKAHEAD) - 1
    if k < 0:
        k = 0
        pos, state = 0, 0
    else:
        pos, state = old.starts[k], old.states[k]
    texts, starts, ends, states = old.texts[:k], old.starts[:k], old.ends[:k], old.states[:k]
    d = len(sql) - len(o)
    tail_from = len(sql) - cs
    hit = []

    def resync(x, st):
        if x < tail_from:
            return False
        y = x - d
        j = bisect_left(old.starts, y)
        if j < len(old.starts) and old.starts[j] == y and old.states[j] == st:
            hit.append(j)
            return True
        return False

    if _scan(sql, pos, state, texts, starts, ends, states, resync):
        j = hit[0]
        texts.extend(old.texts[j:])
        states.extend(old.states[j:])
        if d:
            starts.extend([x + d for x in old.starts[j:]])
            ends.extend([x + d for x in old.ends[j:]])
        else:
            starts.extend(old.starts[j:])
            ends.extend(old.ends[j:])
    return Tokens(sql, texts, starts, ends, states)


def tokenize(sql):
    """Tokens 를 돌려준다(읽기 전용으로 쓸 것). 같은 문자열·조금 바뀐 문자열을 되풀이해 토큰화하는 search 용."""
    t = _recent.get(sql)
    if t is not None:
        _recent.move_to_end(sql)
        return t
    global _last_big
    if len(sql) > 2000:
        t = _tokenize_incremental(sql, _last_big) if _last_big is not None else _tokenize_full(sql)
        _last_big = t
    else:
        t = _tokenize_full(sql)
    _recent[sql] = t
    if len(_recent) > 16:
        _recent.popitem(last=False)
    return t


def tokenize_sql(sql):
    """영숫자·밑줄·@ 연속은 한 토큰, 그 밖의 특수문자는 한 글자씩. 공백과 주석은 토큰이 아니다."""
    return tokenize(sql).to_list()


def _tokenize_reference(sql):
    """Java 코드를 글자 단위로 그대로 옮긴 원래 판 — 빠른 판과 결과 대조용."""
    tokens = []
    start = 0
    cursor = 0
    comment1 = comment2 = False
    in_single = in_double = False
    n = len(sql)
    while cursor < n:
        ch = sql[cursor]
        if comment1:
            if ch == '\n':
                comment1 = False
                start = cursor + 1
        elif comment2:
            if ch == '/' and cursor > 0 and sql[cursor - 1] == '*':
                comment2 = False
                start = cursor + 1
        elif not is_letter_or_digit(ch) and ch != '_' and ch != '@':
            if cursor > start:
                tokens.append(_new_token(start, cursor, sql[start:cursor], in_single or in_double))
            if ch == '-' and char_at(sql, cursor + 1) == '-' and not in_single and not in_double \
                    and (n - cursor < 6 or sql[cursor + 2:cursor + 6] != HINT_KEY_WORD):
                comment1 = True
            elif ch == '/' and char_at(sql, cursor + 1) == '*' and not in_single and not in_double:
                comment2 = True
            elif not is_whitespace(ch):
                tokens.append(_new_token(cursor, cursor + 1, ch, in_single or in_double))
                if ch == "'" and not in_double:
                    in_single = not in_single
                if ch == '"' and not in_single:
                    in_double = not in_double
            start = cursor + 1
        cursor += 1
    if cursor > start and not comment1 and not comment2:
        tokens.append(_new_token(start, cursor, sql[start:cursor], in_single or in_double))
    return tokens


def safe_split(string, delimiter):
    """따옴표 안과 역슬래시 이스케이프를 존중하는 split."""
    if len(string) == 0:
        return ['']
    result = []
    literal = escape = False
    startpos = 0
    for i, cur in enumerate(string):
        if cur == '"' and not escape:
            literal = not literal
        if not literal and cur == delimiter and not escape:
            result.append(string[startpos:i])
            startpos = i + 1
        escape = (not escape) if cur == '\\' else False
    result.append(string[startpos:])
    return result


def split_and_keep(val, regex):
    result = []
    pos = 0
    for m in re.finditer(regex, val):
        result.append(val[pos:m.start()])
        result.append(m.group())
        pos = m.end()
    if pos < len(val):
        result.append(val[pos:])
    return result


def replace_with_concat(val):
    """'a''b' 같은 이스케이프된 작은따옴표 문자열을 CONCAT('a','\\047','b') 로 바꾼다(Impala·BigQuery·Spark)."""
    out = []
    for tok in split_and_keep(val, REGEX_ESCAPED_APOSTROPHES):
        if re.fullmatch(REGEX_ESCAPED_APOSTROPHES, tok) and "''" in tok and tok != "''":
            literals = split_and_keep(tok, "''")
            parts = []
            for lit in literals:
                if lit == "''":
                    parts.append("'\\047'")
                else:
                    s = lit.replace("'", '').replace('\\', '\\\\').replace('"', '\\042').replace('/', '\\/')
                    parts.append("'" + s + "'")
            out.append('CONCAT(' + ','.join(parts) + ')')
        else:
            out.append(tok)
    return ''.join(out)


def split_sql(sql):
    """SqlSplit.splitSql: 여러 문장을 문장 목록으로. BEGIN/CASE … END 중첩과 따옴표·대괄호를 존중."""
    parts = []
    tokens = tokenize_sql(sql.lower())
    nest = []
    last_pop = ''
    start = 0
    quote = bracket = False
    quote_text = ''
    cursor = start
    while cursor < len(tokens):
        token = tokens[cursor]
        t = token.text
        if quote:
            if t == quote_text:
                quote = False
        elif bracket:
            if t == ']':
                bracket = False
        elif t == "'" or t == '"':
            quote = True
            quote_text = t
        elif t == '[':
            bracket = True
        elif t == 'begin' or t == 'case':
            nest.append(t)
        elif t == 'end' and (cursor == len(tokens) - 1 or tokens[cursor + 1].text != 'if'):
            if not nest:
                raise JavaError('EmptyStackException')
            last_pop = nest.pop()
        elif not nest and t == ';':
            if cursor == 0 or (tokens[cursor - 1].text == 'end' and last_pop == 'begin'):
                parts.append(substring(sql, tokens[start].start, token.end))
            else:
                parts.append(substring(sql, tokens[start].start, token.end - 1))
            start = cursor + 1
        cursor += 1
    if start < cursor:
        parts.append(substring(sql, tokens[start].start, tokens[cursor - 1].end))
    return parts
