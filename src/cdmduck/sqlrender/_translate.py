"""org.ohdsi.sql.SqlTranslate 포팅: SQL Server 방언 → 대상 방언(패턴 치환)."""
import os
import random as _random
import re
import threading
from functools import lru_cache

from ._java import (JavaError, hash_order, is_letter, is_letter_or_digit, remove_blank_lines,
                    strip_ws_before_end, substring, trim)
from ._strings import Token, replace_all, replace_with_concat, safe_split, split_sql, tokenize, tokenize_sql

SESSION_ID_LENGTH = 8
MAX_TABLE_NAME_LENGTH = 63
BIG_QUERY, IMPALA, SPARK = 'bigquery', 'impala', 'spark'
_FLAGS = re.I | re.S | re.M | re.A          # Java CASE_INSENSITIVE|DOTALL|MULTILINE (ASCII 대소문자·\w·\s·\b)
_CSV = os.path.join(os.path.dirname(__file__), 'csv', 'replacementPatterns.csv')

_patterns = None
_lock = threading.Lock()
_global_session_id = None


class Block(Token):
    __slots__ = ('is_variable', 'regex')

    def __init__(self, other):
        super().__init__(other)
        self.is_variable = False
        self.regex = None


class MatchedPattern:
    __slots__ = ('start', 'end', 'start_token', 'variable_to_value')

    def __init__(self):
        self.start = self.end = self.start_token = 0
        self.variable_to_value = {}          # 삽입 순서 보존 → hash_order 로 Java HashMap 순서 재현


@lru_cache(maxsize=None)
def _rx(regex):
    return re.compile(regex, _FLAGS)


def parse_search_pattern(pattern):
    return list(_parse_search_pattern(pattern))


@lru_cache(maxsize=4096)
def _parse_search_pattern(pattern):
    tokens = tokenize_sql(pattern.lower())
    blocks = []
    i = 0
    while i < len(tokens):
        block = Block(tokens[i])
        if len(block.text) > 2 and block.text[0] == '@':
            block.is_variable = True
        if block.text == '@@' and i < len(tokens) - 2 and tokens[i + 1].text == '(':
            escape = False
            nesting = 0
            for j in range(i + 2, len(tokens)):
                t = tokens[j].text
                if escape:
                    escape = False
                elif t == '\\':
                    escape = True
                elif t == '(':
                    nesting += 1
                elif t == ')':
                    if nesting == 0:
                        if j + 1 >= len(tokens):
                            raise JavaError('IndexOutOfBoundsException')
                        block.text = '@@' + tokens[j + 1].text
                        block.regex = substring(pattern, tokens[i + 1].end, tokens[j].start)
                        block.end = tokens[j + 1].end
                        block.is_variable = True
                        i = j + 1
                        break
                    nesting -= 1
        blocks.append(block)
        i += 1
    if not blocks:
        raise JavaError('IndexOutOfBoundsException')
    if (blocks[0].is_variable and blocks[0].regex is None) or (blocks[-1].is_variable and blocks[-1].regex is None):
        raise JavaError('Error in search pattern: pattern cannot start or end with a non-regex variable: ' + pattern)
    return tuple(blocks)


def _matches(regex, s):
    return _rx(regex).fullmatch(s) is not None


def _matches_end(regex, s):
    s = strip_ws_before_end(s)
    start = -1
    for m in _rx(regex).finditer(s):
        if m.end() == len(s):
            start = m.start()
    return start


def search(sql, pp, start_token):
    tk = tokenize(sql.lower())
    texts, starts, ends, states = tk.texts, tk.starts, tk.ends, tk.states
    n_tok, n_pp = len(texts), len(pp)
    match_count = 0
    var_start = 0
    nest = []
    in_pq = False
    mp = MatchedPattern()
    vv = mp.variable_to_value
    cursor = start_token
    while cursor < n_tok:
        t_text, t_start = texts[cursor], starts[cursor]
        blk = pp[match_count]
        if blk.is_variable:
            if blk.regex is not None and (match_count == n_pp - 1 or pp[match_count + 1].is_variable):
                # 패턴 끝의 정규식 변수, 또는 뒤에 다른 변수가 오는 정규식 변수
                m = _rx(blk.regex).search(substring(sql, t_start))
                if m is not None and m.start() == 0:
                    if match_count == 0:
                        mp.start = t_start
                        mp.start_token = cursor
                    vv[blk.text] = sql[t_start:t_start + m.end()]
                    match_count += 1
                    if match_count == n_pp:
                        mp.end = t_start + m.end()
                        return mp
                    elif pp[match_count].is_variable:
                        var_start = t_start + m.end()
                    while cursor < n_tok and starts[cursor] < t_start + m.end():
                        cursor += 1
                    cursor -= 1
                else:
                    match_count = 0
            elif not nest and match_count < n_pp - 1 and t_text == pp[match_count + 1].text:
                if blk.regex is not None and match_count == 0:
                    start = _matches_end(blk.regex, substring(sql, var_start, t_start))
                    if start != -1:
                        vv[blk.text] = substring(sql, start + var_start, t_start)
                        mp.start = start + var_start
                        mp.start_token = cursor
                        match_count += 2
                        if match_count == n_pp:
                            mp.end = ends[cursor]
                            return mp
                        elif pp[match_count].is_variable:
                            var_start = starts[cursor + 1] if cursor < n_tok - 1 else -1
                        if t_text == "'":
                            in_pq = not in_pq
                elif blk.regex is not None and not _matches(blk.regex, substring(sql, var_start, t_start)):
                    match_count = 0
                    cursor = mp.start_token
                else:
                    vv[blk.text] = substring(sql, var_start, t_start)
                    match_count += 2
                    if match_count == n_pp:
                        mp.end = ends[cursor]
                        return mp
                    elif pp[match_count].is_variable:
                        var_start = starts[cursor + 1] if cursor < n_tok - 1 else -1
                    if t_text == "'":
                        in_pq = not in_pq
            elif match_count != 0 and not nest and not in_pq and t_text in (';', ')'):
                # 여러 문장이나 괄호 밖으로 걸쳐 일치할 수 없다
                match_count = 0
                cursor = mp.start_token
            else:
                if nest and nest[-1] in ('"', "'"):
                    if t_text == nest[-1]:
                        nest.pop()
                elif t_text in ('"', "'"):
                    nest.append(t_text)
                elif not in_pq and t_text == '(':
                    nest.append(t_text)
                elif not in_pq and nest and t_text == ')' and nest[-1] == '(':
                    nest.pop()
        else:
            # 패턴의 첫 부분은 따옴표 안에서 시작할 수 없다
            if t_text == blk.text and (match_count != 0 or not states[cursor]):
                if match_count == 0:
                    mp.start = t_start
                    mp.start_token = cursor
                match_count += 1
                if match_count == n_pp:
                    mp.end = ends[cursor]
                    return mp
                elif pp[match_count].is_variable:
                    var_start = starts[cursor + 1] if cursor < n_tok - 1 else -1
                if t_text in ("'", '"'):
                    in_pq = not in_pq
            elif match_count != 0:
                match_count = 0
                cursor = mp.start_token
        if match_count != 0 and cursor == n_tok - 1:
            match_count = 0
            cursor = mp.start_token
        cursor += 1
    mp.start = -1
    return mp


def _search_and_replace(sql, pp, replace_pattern):
    mp = search(sql, pp, 0)
    while mp.start != -1:
        replacement = replace_pattern
        vv = mp.variable_to_value
        for k in hash_order(list(vv)):
            replacement = replace_all(replacement, k, vv[k])
        sql = substring(sql, 0, mp.start) + replacement + substring(sql, mp.end)
        delta = 1 if len(tokenize(replacement)) else 0
        # 치환문이 변수로 시작하고 그 값이 검색 패턴 첫 토큰으로 시작하면 첫 토큰을 건너뛰지 않는다
        if delta > 0 and replace_pattern.startswith('@@') and trim(replacement.lower()).startswith(pp[0].text):
            delta = 0
        mp = search(sql, pp, mp.start_token + delta)
    return sql


def _translate(sql, patterns, session_id, temp_prefix):
    for search_p, repl_p in patterns:
        repl_p = repl_p.replace('%session_id%', session_id).replace('%temp_prefix%', temp_prefix)
        sql = remove_blank_lines(_search_and_replace(sql, _parse_search_pattern(search_p), repl_p))
    return remove_blank_lines(sql)


def _line2columns(line):
    cols = safe_split(line, ',')
    out = []
    for c in cols:
        if c.startswith('"') and c.endswith('"') and len(c) > 1:
            c = c[1:-1]
        out.append(c.replace('\\"', '"').replace('\\n', '\n'))
    return out


def load_patterns(path=None):
    """{대상방언: [(검색, 치환), …]} — 입력 순서 유지. Java readLine 처럼 \\r\\n·\\r·\\n 만 줄 구분."""
    with open(path or _CSV, encoding='utf-8', newline='') as f:
        text = f.read()
    lines = re.split(r'\r\n|\r|\n', text)
    if lines and lines[-1] == '':
        lines.pop()
    pats = {}
    for line in lines[1:]:
        row = _line2columns(line)
        if len(row) < 3:
            raise JavaError('IndexOutOfBoundsException')
        pats.setdefault(row[0], []).append((row[1].replace('@', '@@'), row[2].replace('@', '@@')))
    return pats


def set_replacement_patterns(path=None):
    global _patterns
    with _lock:
        _patterns = load_patterns(path)


def _ensure_patterns():
    global _patterns
    if _patterns is None:
        with _lock:
            if _patterns is None:
                _patterns = load_patterns()
    return _patterns


def dialects():
    return list(_ensure_patterns())


def _validate_session_id(sid):
    if len(sid) != SESSION_ID_LENGTH:
        raise JavaError(f'Session ID has length {len(sid)}, should be {SESSION_ID_LENGTH}')
    if not is_letter(sid[0]):
        raise JavaError('Session ID does not start with a letter')
    for ch in sid[1:]:
        if not is_letter_or_digit(ch):
            raise JavaError('Illegal character in session ID')


def generate_session_id():
    chars = 'abcdefghijklmnopqrstuvwxyz0123456789'
    return _random.choice(chars[:26]) + ''.join(_random.choice(chars) for _ in range(SESSION_ID_LENGTH - 1))


def get_global_session_id():
    global _global_session_id
    if _global_session_id is None:
        _global_session_id = generate_session_id()
    return _global_session_id


def translate_sql(sql, target_dialect, session_id=None, temp_emulation_schema=None):
    pats = _ensure_patterns()
    if session_id is None:
        session_id = get_global_session_id()
    else:
        _validate_session_id(session_id)
    temp_prefix = '' if temp_emulation_schema is None else temp_emulation_schema + '.'
    patterns = pats.get(target_dialect)
    if patterns is None:
        raise JavaError("Don't know how to translate to " + str(target_dialect)
                        + '. Valid target dialects are ' + ', '.join(hash_order(list(pats))))
    if target_dialect.lower() == BIG_QUERY:
        from ._bigquery_spark import translate_bigquery
        sql = translate_bigquery(sql)
    elif target_dialect.lower() == SPARK:
        from ._bigquery_spark import translate_spark
        sql = translate_spark(sql)
    sql = _translate(sql, patterns, session_id, temp_prefix)
    if target_dialect.lower() in (IMPALA, BIG_QUERY) or target_dialect == SPARK:
        sql = replace_with_concat(sql)
    return sql


def translate_single_statement_sql(sql, target_dialect, session_id=None, temp_emulation_schema=None):
    sql = translate_sql(sql, target_dialect, session_id, temp_emulation_schema)
    parts = split_sql(sql)
    if len(parts) > 1:
        raise JavaError('SQL contains more than one statement: ' + sql)
    if not parts:
        raise JavaError('ArrayIndexOutOfBoundsException')
    return parts[0]


def check(sql, target_dialect=None):
    warnings = []
    long_temp = []
    for m in re.finditer(r'#[0-9a-zA-Z_]+', sql):
        if len(m.group()) > MAX_TABLE_NAME_LENGTH - SESSION_ID_LENGTH - 1 and m.group() not in long_temp:
            long_temp.append(m.group())
    for name in hash_order(long_temp):
        warnings.append(f"Temp table name '{name}' is too long. Temp table names should be shorter than "
                        f'{MAX_TABLE_NAME_LENGTH - SESSION_ID_LENGTH} characters to prevent some DMBSs from throwing an error.')
    long_names = []
    for m in re.finditer(r'(create|drop|truncate)[ \t\n\x0b\f\r]+table +[0-9a-zA-Z_]+', sql.lower()):
        name = sql[m.start() + m.group().rfind(' '):m.end()]
        if len(name) > MAX_TABLE_NAME_LENGTH and '#' + name not in long_temp and name not in long_names:
            long_names.append(name)
    for name in hash_order(long_names):
        warnings.append(f"Table name '{name}' is too long. Table names should be shorter than "
                        f'{MAX_TABLE_NAME_LENGTH} characters to prevent some DMBSs from throwing an error.')
    return warnings
