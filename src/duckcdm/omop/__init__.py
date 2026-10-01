"""OMOP CDM 표 정의(OHDSI CommonDataModel 5.3·5.4 필드 목록)와 parquet 디렉터리 → DuckDB 뷰.

parquet 에 있는 표준 밖의 열(원천 식별번호 등)은 뷰에 넣지 않는다. 표준 열이 parquet 에 없으면 NULL 로 채운다.
"""
import csv
import os
import re

_DIR = os.path.dirname(__file__)
RESULTS_TABLES = {'cohort', 'cohort_definition'}
_TYPES = {'integer': 'INTEGER', 'bigint': 'BIGINT', 'float': 'DOUBLE', 'date': 'DATE', 'datetime': 'TIMESTAMP'}


def cdm_fields(versions=('5.4', '5.3')):
    """{table: [(field, duckdb type), ...]} — 여러 버전의 합집합(5.4 순서 우선)."""
    out = {}
    with open(os.path.join(_DIR, 'cdm_fields.csv'), encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    for v in versions:
        for r in rows:
            if r['version'] != v or not r['field'].strip() or not r['table'].strip():
                continue
            cols = out.setdefault(r['table'], [])
            if r['field'] not in [c for c, _ in cols]:
                cols.append((r['field'], _TYPES.get(r['datatype'], 'VARCHAR')))
    return out


def _source(root, table):
    p = os.path.join(root, table)
    if os.path.isdir(p):
        has = any(n.endswith('.parquet') for _, _, files in os.walk(p) for n in files)
        return f"{os.path.join(p, '**', '*.parquet')}" if has else None
    if os.path.isfile(p + '.parquet'):
        return p + '.parquet'
    return None


def create_parquet_views(con, root, schema='main', versions=('5.4', '5.3'), log=print):
    """root/<table>/(**/)*.parquet 또는 root/<table>.parquet → schema.<table> 뷰. 돌려주는 값: {table: 제외한 열}."""
    fields = cdm_fields(versions)
    con.execute(f'CREATE SCHEMA IF NOT EXISTS {schema}')
    excluded = {}
    for table, cols in fields.items():
        if table in RESULTS_TABLES:            # 결과 스키마 쪽 표 — CDM 뷰로 만들지 않는다
            continue
        src = _source(root, table)
        if src is None:
            sel = ', '.join(f'CAST(NULL AS {t}) AS "{c}"' for c, t in cols)
            con.execute(f'CREATE OR REPLACE VIEW {schema}.{table} AS SELECT {sel} FROM (SELECT 1) z WHERE 1 = 0')
            if log:
                log(f'  {table:<24} (없음 → 빈 뷰)')
            continue
        reader = f"read_parquet('{src}', hive_partitioning=false, union_by_name=true)"
        have = {r[0].lower(): r[0] for r in con.execute(f'DESCRIBE SELECT * FROM {reader}').fetchall()}
        sel = []
        for c, t in cols:
            sel.append(f'"{have[c]}" AS "{c}"' if c in have else f'CAST(NULL AS {t}) AS "{c}"')
        excluded[table] = sorted(set(have) - {c for c, _ in cols})
        con.execute(f'CREATE OR REPLACE VIEW {schema}.{table} AS SELECT {", ".join(sel)} FROM {reader}')
        if log:
            log(f'  {table:<24} 열 {len(cols)}' + (f'  제외: {", ".join(excluded[table])}' if excluded[table] else ''))
    return excluded


def safe_name(s):
    return re.sub(r'[^A-Za-z0-9_]', '_', s)
