"""CDM 데이터 소스: DuckDB 파일 하나 = WebAPI 의 Source 하나(CDM·Vocabulary·Results 다이몬)."""
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
    """SQL 문자열 리터럴."""
    if s is None:
        return 'NULL'
    return "'" + str(s).replace("'", "''") + "'"


def q_in(values):
    return ','.join(q(v) for v in values)


def like_text(s):
    """LIKE '%@x%' 처럼 이미 따옴표 안에 들어가는 자리의 글자."""
    return str(s).replace("'", "''")


class Source:
    """하나의 DuckDB 파일. 쓰기 연결은 하나를 잠금으로 공유하고(DuckDB 는 프로세스 안 다중 연결은 cursor 로),
    읽기는 cursor() 로 나눠 쓴다."""

    def __init__(self, source_id, key, name, path, cdm_schema='main', vocab_schema=None, results_schema='results',
                 read_only=False):
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
        if not read_only:
            self.init_results()

    # -- 연결
    def cursor(self):
        return self.con.cursor()

    def execute_script(self, sql, cur=None):
        """OHDSI SQL(SQL Server 방언, @파라미터 이미 채움) → duckdb 로 번역해 문장별로 실행."""
        c = cur or self.cursor()
        for stmt in split_sql(translate(sql, self.dialect)):
            if stmt.strip():
                c.execute(stmt)
        return c

    def query(self, sql, **params):
        """렌더 → 번역 → 실행, (columns, rows)."""
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

    # -- 결과 스키마
    def init_results(self):
        with self.lock:
            cur = self.cursor()
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS {self.results_schema}')
            for name in ('cohort', 'cohort_inclusion', 'cohort_inclusion_result', 'cohort_inclusion_stats',
                         'cohort_summary_stats', 'cohort_censor_stats'):
                ddl = sql_resource('results', name + '.sql')
                # "IF OBJECT_ID(...) IS NULL CREATE TABLE" 는 SQL Server 전용 — 번역기가 못 다루므로 직접 처리
                body = ddl[ddl.index('CREATE TABLE'):]
                body = body.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1)
                self.execute_script(render(body, results_schema=self.results_schema), cur)

    # -- WebAPI 모양
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
