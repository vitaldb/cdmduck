"""org.ohdsi.sql.BigQuerySparkTranslate port (excluding sparkHandleInsert, which needs a DB connection)."""
import re

from ._java import JavaError, split, substring, trim
from ._strings import split_sql, tokenize_sql
from ._translate import parse_search_pattern, search

SELECT, GROUP_BY, ORDER_BY, WITH_COLUMNS, IN = range(5)


class _CommaListIterator:
    def __init__(self, expression_list, list_type):
        self.list_type = list_type
        self.expression_list = expression_list
        self._split_list()
        self.expression_list = ',' + self.expression_list + ','
        self.pattern = parse_search_pattern(', @@a ,')
        self.cur = search(self.expression_list, self.pattern, 0)
        if self.cur.start != -1:
            self._split_expression()

    def done(self):
        return self.cur.start == -1

    def next(self):
        n = len(tokenize_sql(substring(self.expression_list, self.cur.start, self.cur.end)))
        self.cur = search(self.expression_list, self.pattern, self.cur.start_token + n - 1)
        if self.cur.start != -1:
            self._split_expression()

    def full(self):
        return _s(self.prefix) + _s(self.suffix)

    def _split_list(self):
        self.list_prefix = ''
        self.list_suffix = ''
        if self.list_type == SELECT:
            m = search('^' + _s(self.expression_list) + '$', parse_search_pattern('^ distinct @@a $'), 0)
            if m.start != -1:
                self.list_prefix = 'distinct '
                self.expression_list = m.variable_to_value.get('@@a')
            m = search('^' + _s(self.expression_list) + '$', parse_search_pattern('^@@a into @@b$'), 0)
            if m.start != -1:
                self.expression_list = m.variable_to_value.get('@@a')
                self.list_suffix = ' into ' + _s(m.variable_to_value.get('@@b'))
        elif self.list_type == GROUP_BY:
            m = search('^' + _s(self.expression_list) + '$', parse_search_pattern('^@@a order by @@b$'), 0)
            if m.start != -1:
                self.expression_list = m.variable_to_value.get('@@a')
                self.list_suffix = ' order by ' + _s(m.variable_to_value.get('@@b'))

    def _split_expression(self):
        self.prefix = self.cur.variable_to_value.get('@@a')
        self.suffix = ''
        if self.list_type == SELECT:
            self._split_alias()
        elif self.list_type == ORDER_BY:
            tokens = tokenize_sql(self.full())
            if not tokens:
                raise JavaError('IndexOutOfBoundsException')
            last = tokens[-1]
            if last.text.lower() in ('asc', 'desc'):
                self.prefix = substring(self.full(), 0, last.start - 1)
                self.suffix = ' ' + last.text

    def _split_alias(self):
        tokens = tokenize_sql(self.prefix)
        m = search('^' + self.prefix + '$', parse_search_pattern('^ @@a as @@b $'), 0)
        if m.start == -1:
            if len(tokens) >= 2:
                alias = tokens[-1]
                preceding = tokens[-2].text
                if alias.is_identifier() and preceding != '.' and preceding != '+':
                    self.prefix = substring(self.prefix, 0, alias.start)
                    self.suffix = alias.text
        else:
            self.prefix = m.variable_to_value.get('@@a')
            self.suffix = m.variable_to_value.get('@@b')

    def is_single_column_reference(self):
        t = tokenize_sql(self.prefix)
        return len(t) == 3 and t[0].is_identifier() and t[1].text == '.' and t[2].is_identifier()


def _s(x):
    """Java string concatenation: null becomes "null"."""
    return 'null' if x is None else x


def _alias_ctes(sql, pattern):
    pp = parse_search_pattern(pattern)
    m = search(sql, pp, 0)
    while m.start != -1:
        vv = m.variable_to_value
        with_it = _CommaListIterator(vv.get('@@b'), WITH_COLUMNS)
        sel_it = _CommaListIterator(vv.get('@@c'), SELECT)
        repl = ''
        while not with_it.done():
            if sel_it.done():
                break
            expr = _s(sel_it.prefix) + ' as ' + with_it.full()
            if repl:
                repl += ','
            repl += expr
            with_it.next()
            sel_it.next()
        repl = sel_it.list_prefix + repl + sel_it.list_suffix
        a = vv.get('@@a')
        if a is None:
            raise JavaError('NullPointerException')
        body = pattern.replace('@@a', a).replace('(@@b)', '').replace('@@c', repl).replace('@@d', vv.get('@@d') or '')
        sql = substring(sql, 0, m.start) + body + substring(sql, m.end)
        m = search(sql, pp, m.start_token + 1)
    return sql


def _convert_select_list_references(sql, select_pattern, list_type):
    pp = parse_search_pattern(select_pattern)
    m = search(sql, pp, 0)
    while m.start != -1:
        vv = m.variable_to_value
        select_list = vv.get('@@s')
        repl = ''
        it = _CommaListIterator(vv.get('@@r'), list_type)
        while not it.done():
            expr, suffix = it.prefix, it.suffix
            expr_pp = parse_search_pattern(expr)
            if it.is_single_column_reference():
                repl += ', ' + expr + suffix
            else:
                sel_it = _CommaListIterator(select_list, SELECT)
                found = False
                i = 1
                while not sel_it.done():
                    if search(sel_it.prefix, expr_pp, 0).start != -1:
                        found = True
                        repl += ', ' + str(i) + suffix
                        break
                    i += 1
                    sel_it.next()
                if not found:
                    repl += ', ' + expr + suffix
            it.next()
        repl = it.list_prefix + substring(repl, 1) + it.list_suffix
        tail = substring(sql, m.end)
        sql = substring(sql, 0, m.start)
        for block in pp:
            if sql:
                sql += ' '
            if block.is_variable:
                sql += repl if block.text == '@@r' else _s(vv.get(block.text))
            else:
                sql += block.text
        sql += tail
        m = search(sql, pp, m.start_token + 1)
    return sql


def _lower_case(sql):
    for t in tokenize_sql(sql):
        if not t.in_quotes and not t.text.startswith('@'):
            sql = substring(sql, 0, t.start) + t.text.lower() + substring(sql, t.end)
    return sql


def translate_bigquery(sql):
    sql = _lower_case(sql)
    sql = _alias_ctes(sql, 'with @@a (@@b) as (select @@c from @@d)')
    sql = _alias_ctes(sql, 'with @@a (@@b) as (select @@c union @@d)')
    sql = _alias_ctes(sql, 'with @@a (@@b) as (select @@c)')
    sql = _alias_ctes(sql, ', @@a (@@b) as (select @@c from @@d)')
    g = 'select @@s from @@b group by @@r'
    for suffix in (';', ')', ' having', ' order by'):
        sql = _convert_select_list_references(sql, g + suffix, GROUP_BY)
    o = 'select @@s from @@b order by @@r'
    for suffix in (';', ')'):
        sql = _convert_select_list_references(sql, o + suffix, ORDER_BY)
    return sql


def _spark_create_table(sql):
    if not sql.endswith(';'):
        sql += ';'
    pp = parse_search_pattern('CREATE TABLE @@table (@@definition)')
    sql = re.sub(' +', ' ', trim(sql).replace('\t', ' '))
    m = search(sql, pp, 0)
    table = m.variable_to_value.get('@@table')
    definition = m.variable_to_value.get('@@definition')
    if table is not None and definition is not None:
        table = table.replace('\r\n', '')
        definition = definition.lower().replace('\r\n', '').replace(' as ', ' ')
        cols = []
        for f in split(definition, ','):
            parts = split(trim(f), ' ')
            if len(parts) < 2:
                raise JavaError('ArrayIndexOutOfBoundsException')
            cols.append('\tCAST(NULL AS ' + parts[1] + ') AS ' + parts[0])
        sql = substring(sql, 0, m.start) + 'SELECT ' + ',\r\n'.join(cols) + ' INTO ' + table + ' WHERE 1 = 0'
    return sql.replace(';', '')


def translate_spark(sql):
    parts = [_spark_create_table(p) for p in split_sql(sql)]
    joined = trim(';\r\n'.join(parts))
    if len(parts) > 1 or trim(sql).endswith(';'):
        return joined + ';'
    return joined
