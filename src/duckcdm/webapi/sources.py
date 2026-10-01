"""CDM data sources: one DuckDB file = one WebAPI Source (CDM, Vocabulary and Results daimons)."""
import os
import threading

import duckdb

from ..sqlrender import render, split_sql, translate

_SQL_DIR = os.path.join(os.path.dirname(__file__), 'sql')
_sql_cache = {}


def sql_resource(*parts):
    key = '/'.join(parts)
    t = _sql_cache.get(key)
    if t is None:
        with open(os.path.join(_SQL_DIR, *parts), encoding='utf-8') as f:
            t = _sql_cache[key] = f.read()
    return t


def q(s):
    """SQL string literal."""
    if s is None:
        return 'NULL'
    return "'" + str(s).replace("'", "''") + "'"


def q_in(values):
    return ','.join(q(v) for v in values)


def like_text(s):
    """Text for a spot already inside quotes, as in LIKE '%@x%'."""
    return str(s).replace("'", "''")


class Source:
    """One DuckDB file. A single write connection is shared under a lock (within a process DuckDB does multiple
    connections via cursors); reads each get their own cursor()."""

    def __init__(self, source_id, key, name, path, cdm_schema='main', vocab_schema=None, results_schema='results',
                 read_only=False, memory_limit=None, threads=None):
        self.source_id = source_id
        self.key = key
        self.name = name
        self.path = path
        self.cdm_schema = cdm_schema
        self.vocab_schema = vocab_schema or cdm_schema
        self.results_schema = results_schema
        self.dialect = 'duckdb'
        self.lock = threading.RLock()
        self.con = duckdb.connect(path, read_only=read_only)
        if memory_limit:
            self.con.execute(f"SET memory_limit = '{memory_limit}'")
        if threads:
            self.con.execute(f'SET threads = {int(threads)}')
        if not read_only:
            self.init_results()

    # -- connection
    def cursor(self):
        return self.con.cursor()

    def execute_script(self, sql, cur=None):
        """OHDSI SQL (SQL Server dialect, @parameters already filled) -> translate to duckdb and run statement by statement."""
        c = cur or self.cursor()
        for stmt in split_sql(translate(sql, self.dialect)):
            if stmt.strip():
                c.execute(stmt)
        return c

    def query(self, sql, **params):
        """Render -> translate -> execute; returns (columns, rows)."""
        rendered = render(sql, **params)
        duck = translate(rendered, self.dialect)
        cur = self.cursor()
        parts = [s for s in split_sql(duck) if s.strip()]
        for stmt in parts[:-1]:
            cur.execute(stmt)
        cur.execute(parts[-1])
        cols = [d[0] for d in cur.description]
        return cols, cur.fetchall()

    def query_dicts(self, sql, **params):
        cols, rows = self.query(sql, **params)
        return [dict(zip(cols, r)) for r in rows]

    # -- results schema
    def init_results(self):
        with self.lock:
            cur = self.cursor()
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS {self.results_schema}')
            for name in ('cohort', 'cohort_inclusion', 'cohort_inclusion_result', 'cohort_inclusion_stats',
                         'cohort_summary_stats', 'cohort_censor_stats'):
                ddl = sql_resource('results', name + '.sql')
                # "IF OBJECT_ID(...) IS NULL CREATE TABLE" is SQL Server only — the translator cannot handle it, so do it directly
                body = ddl[ddl.index('CREATE TABLE'):]
                body = body.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1)
                self.execute_script(render(body, results_schema=self.results_schema), cur)

    # -- WebAPI shapes
    def info(self):
        return {
            'sourceId': self.source_id, 'sourceKey': self.key, 'sourceName': self.name, 'sourceDialect': self.dialect,
            'daimons': [
                {'sourceDaimonId': self.source_id * 10 + 1, 'daimonType': 'CDM', 'tableQualifier': self.cdm_schema,
                 'priority': 1},
                {'sourceDaimonId': self.source_id * 10 + 2, 'daimonType': 'Vocabulary',
                 'tableQualifier': self.vocab_schema, 'priority': 1},
                {'sourceDaimonId': self.source_id * 10 + 3, 'daimonType': 'Results',
                 'tableQualifier': self.results_schema, 'priority': 1},
            ],
        }

    def details(self):
        d = self.info()
        d.update({'connectionString': 'duckdb:' + self.path, 'username': None, 'password': None,
                  'krbAuthMethod': 'password', 'krbAdminServer': None, 'keytabName': None})
        return d
