"""org.ohdsi.sql.SqlRender 포팅: @파라미터 치환, {DEFAULT …}, {조건} ? {참} : {거짓}."""
import re

from ._java import JavaError, hash_order, is_whitespace, split, substring, trim
from ._strings import replace as _str_replace

_JAVA_WS = '[ \\t\\n\\x0b\\f\\r]'
_DEFAULTS_RE = re.compile(r'\{DEFAULT[^}]*\}' + _JAVA_WS + '*\n?')


class _Span:
    __slots__ = ('start', 'end', 'valid')

    def __init__(self, start, end):
        self.start, self.end, self.valid = start, end, True


class _IfThenElse:
    __slots__ = ('condition', 'if_true', 'if_false', 'has_if_false')

    def __init__(self):
        self.condition = self.if_true = self.if_false = None
        self.has_if_false = False

    def start(self):
        return self.condition.start

    def end(self):
        return self.if_false.end if self.has_if_false else self.if_true.end


def _find_spans(s, open_ch, close_ch):
    starts, spans = [], []
    for i, ch in enumerate(s):
        if ch == open_ch:
            starts.append(i)
        elif ch == close_ch and starts:
            spans.append(_Span(starts.pop(), i + 1))
    return spans


def _link_if_then_elses(s, spans):
    # Java 와 같이 break 없이 전부 훑는다: 한 조건에 '?' 짝이 여럿이면 여럿 만들고, ':' 는 마지막 것이 이긴다
    out = []
    n = len(spans)
    if n > 1:
        for i in range(n - 1):
            for j in range(i + 1, n):
                if spans[j].start > spans[i].end:
                    if trim(s[spans[i].end:spans[j].start]) == '?':
                        ite = _IfThenElse()
                        ite.condition = spans[i]
                        ite.if_true = spans[j]
                        for k in range(j + 1, n):
                            if spans[k].start > spans[j].end:
                                if trim(s[spans[j].end:spans[k].start]) == ':':
                                    ite.if_false = spans[k]
                                    ite.has_if_false = True
                        out.append(ite)
    return out


def _remove_parentheses(s):
    if len(s) > 1 and ((s[0] == "'" and s[-1] == "'") or (s[0] == '"' and s[-1] == '"')):
        return s[1:-1]
    return s


def _preceded_by_in(start, s):
    s = s.lower()
    matched = 0
    for i in range(start - 1, -1, -1):
        ch = s[i]
        if not is_whitespace(ch):
            if matched == 0 and ch == 'n':
                matched += 1
            elif matched == 1 and ch == 'i':
                matched += 1
            else:
                return False
        elif matched == 2:
            return True
    return False


def _evaluate_primitive(s):
    s = trim(s)
    lc = s.lower()
    if lc in ('false', '0', '!true', '!1'):
        return False
    if lc in ('true', '1', '!false', '!0'):
        return True
    found = s.find('==')
    if found != -1:
        left = _remove_parentheses(trim(s[:found]))
        right = _remove_parentheses(trim(s[found + 2:]))
        return left == right
    found = s.find('!=')
    if found == -1:
        found = s.find('<>')
    if found != -1:
        left = _remove_parentheses(trim(s[:found]))
        right = _remove_parentheses(trim(s[found + 2:]))
        return left != right
    found = lc.find(' in ')
    if found != -1:
        left = _remove_parentheses(trim(substring(s, 0, found)))
        right = trim(substring(s, found + 4))
        if len(right) > 2 and right[0] == '(' and right[-1] == ')':
            for part in split(right[1:-1], ','):
                if left == _remove_parentheses(part):     # Java 도 part 를 trim 하지 않는다
                    return True
            return False
    raise JavaError('Error parsing boolean condition: "' + s + '"')


def _evaluate_boolean(s):
    s = trim(s)
    if '&' in s:
        return all(_evaluate_primitive(p) for p in split(s, '&'))
    if '|' in s:
        return any(_evaluate_primitive(p) for p in split(s, r'\|'))
    return _evaluate_primitive(s)


def _replace(s, spans, to_start, to_end, with_start, with_end):
    with_str = substring(s, with_start, with_end + 1)
    s = _str_replace(s, to_start, to_end, with_str)
    for sp in spans:
        if sp.valid:
            if sp.start > to_start:
                if with_start <= sp.start < with_end:
                    delta = to_start - with_start
                    sp.start += delta
                    sp.end += delta
                elif sp.start > to_end:
                    delta = to_start - to_end + len(with_str)
                    sp.start += delta
                    sp.end += delta
                else:
                    sp.valid = False
            elif sp.end > to_end:
                sp.end += to_start - to_end + len(with_str)
    return s


def _evaluate_condition(s):
    s = trim(s)
    spans = _find_spans(s, '(', ')')
    # 닫는 괄호 순서라 안쪽 괄호가 먼저 처리된다. Java 도 여기서는 valid 를 보지 않는다
    for sp in spans:
        if not _preceded_by_in(sp.start, s):
            ev = _evaluate_boolean(substring(s, sp.start + 1, sp.end - 1))
            s = substring(s, 0, sp.start) + ('1' if ev else '0') + substring(s, sp.start + 1)
            s = _replace(s, spans, sp.start, sp.end, sp.start, sp.start)
    return _evaluate_boolean(s)


def _extract_defaults(s):
    defaults = {}
    pre, post = '{DEFAULT ', '}'
    d_start = d_end = 0
    while d_start != -1 and d_end != -1:
        d_start = s.find(pre, d_end)
        if d_start != -1:
            d_end = s.find(post, d_start + len(pre))
            if d_end != -1:
                span = s[d_start + len(pre):d_end]
                found = span.find('=')
                if found != -1:
                    param = trim(span[:found])
                    if param and param[0] == '@':
                        param = param[1:]
                    defaults[param] = _remove_parentheses(trim(substring(span, found + 2)))
    return defaults


def _substitute_parameters(s, params):
    """params: 삽입 순서를 지닌 dict (Java HashMap 에 넣은 순서)."""
    defaults = _extract_defaults(s)
    s = _DEFAULTS_RE.sub('', s)
    params = dict(params)
    for k in hash_order(list(defaults)):
        if k not in params:
            params[k] = defaults[k]
    # HashMap 순회 순서에서 키 길이 내림차순 안정 정렬 → 값 속의 @다른키 도 Java 와 같은 순서로 치환된다
    keys = sorted(hash_order(list(params)), key=len, reverse=True)
    for k in keys:
        v = params[k]
        s = re.sub('@' + k, lambda m, v=v: v, s)
    return s


def _parse_if_then_else(s):
    spans = _find_spans(s, '{', '}')
    ites = _link_if_then_elses(s, spans)
    result = s
    for ite in ites:
        if ite.condition.valid:
            cond = substring(result, ite.condition.start + 1, ite.condition.end - 1)
            if _evaluate_condition(cond):
                result = _replace(result, spans, ite.start(), ite.end(), ite.if_true.start + 1, ite.if_true.end - 2)
            elif ite.has_if_false:
                result = _replace(result, spans, ite.start(), ite.end(), ite.if_false.start + 1, ite.if_false.end - 2)
            else:
                result = _replace(result, spans, ite.start(), ite.end(), 0, -1)
    return result


def render_sql(sql, parameters=None, values=None):
    """SqlRender.renderSql(sql, String[] parameters, String[] values) 와 같은 결과."""
    params = {}
    if parameters is not None:
        for p, v in zip(parameters, values):
            params[p] = v
    return _parse_if_then_else(_substitute_parameters(sql, params))


def check(sql, parameters=None, values=None):
    return [f"Parameter '{p}' not found in SQL" for p in (parameters or []) if '@' + p not in sql]
