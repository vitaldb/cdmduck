"""OMOP CDM table definitions (OHDSI CommonDataModel 5.3/5.4 field lists) and parquet directory -> DuckDB views.

Non-standard columns in the parquet (source identifiers, etc.) are left out of the views. Standard columns missing from the parquet are filled with NULL.
"""
import csv
import os
import re

_DIR = os.path.dirname(__file__)
RESULTS_TABLES = {'cohort', 'cohort_definition'}
_TYPES = {'integer': 'INTEGER', 'bigint': 'BIGINT', 'float': 'DOUBLE', 'date': 'DATE', 'datetime': 'TIMESTAMP'}


def cdm_fields(versions=('5.4', '5.3')):
    """{table: [(field, duckdb type), ...]} — union over versions (5.4 order first)."""
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


def create_parquet_views(con, root, schema='main', versions=('5.4', '5.3'), log=print, materialize=False):
    """root/<table>/(**/)*.parquet or root/<table>.parquet -> view schema.<table>. Returns {table: excluded columns}."""
    fields = cdm_fields(versions)
    con.execute(f'CREATE SCHEMA IF NOT EXISTS {schema}')
    excluded = {}
    for table, cols in fields.items():
        if table in RESULTS_TABLES:            # results-schema table — not made into a CDM view
            continue
        src = _source(root, table)
        if src is None:
            sel = ', '.join(f'CAST(NULL AS {t}) AS "{c}"' for c, t in cols)
            kind = 'TABLE' if materialize else 'VIEW'
            con.execute(f'CREATE OR REPLACE {kind} {schema}.{table} AS SELECT {sel} FROM (SELECT 1) z WHERE 1 = 0')
            if log:
                log(f'  {table:<24} (missing -> empty view)')
            continue
        reader = f"read_parquet('{src}', hive_partitioning=false, union_by_name=true)"
        have = {r[0].lower(): r[0] for r in con.execute(f'DESCRIBE SELECT * FROM {reader}').fetchall()}
        sel = []
        for c, t in cols:
            sel.append(f'"{have[c]}" AS "{c}"' if c in have else f'CAST(NULL AS {t}) AS "{c}"')
        excluded[table] = sorted(set(have) - {c for c, _ in cols})
        if materialize:
            import time
            t0 = time.time()
            con.execute(f'CREATE OR REPLACE TABLE {schema}.{table} AS SELECT {", ".join(sel)} FROM {reader}')
            n = con.execute(f'SELECT count(*) FROM {schema}.{table}').fetchone()[0]
            if log:
                log(f'  {table:<24} table: {n:,} rows, {time.time() - t0:.0f}s')
        else:
            con.execute(f'CREATE OR REPLACE VIEW {schema}.{table} AS SELECT {", ".join(sel)} FROM {reader}')
        if log:
            log(f'  {table:<24} columns {len(cols)}' + (f'  excluded: {", ".join(excluded[table])}' if excluded[table] else ''))
    return excluded


def safe_name(s):
    return re.sub(r'[^A-Za-z0-9_]', '_', s)
