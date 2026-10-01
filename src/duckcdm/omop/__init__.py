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


# Sort keys for export_parquet: the domain concept first (cohort SQL filters by concept sets, so sorted
# row groups let DuckDB skip most of a table), then person_id.
SORT_KEYS = {
    'person': ['person_id'], 'observation_period': ['person_id'], 'death': ['person_id'],
    'visit_occurrence': ['visit_concept_id', 'person_id'], 'visit_detail': ['visit_detail_concept_id', 'person_id'],
    'condition_occurrence': ['condition_concept_id', 'person_id'], 'drug_exposure': ['drug_concept_id', 'person_id'],
    'procedure_occurrence': ['procedure_concept_id', 'person_id'], 'device_exposure': ['device_concept_id', 'person_id'],
    'measurement': ['measurement_concept_id', 'person_id'], 'observation': ['observation_concept_id', 'person_id'],
    'specimen': ['specimen_concept_id', 'person_id'], 'note': ['person_id'],
    'condition_era': ['condition_concept_id', 'person_id'], 'drug_era': ['drug_concept_id', 'person_id'],
    'dose_era': ['drug_concept_id', 'person_id'],
    'concept': ['concept_id'], 'concept_ancestor': ['ancestor_concept_id', 'descendant_concept_id'],
    'concept_relationship': ['concept_id_1', 'relationship_id'], 'concept_synonym': ['concept_id'],
    'source_to_concept_map': ['source_code'], 'drug_strength': ['drug_concept_id'],
}
# Year partitioning by the event date, so each year can be sorted on its own.
PARTITION_DATE = {
    'visit_occurrence': 'visit_start_date', 'visit_detail': 'visit_detail_start_date',
    'condition_occurrence': 'condition_start_date', 'drug_exposure': 'drug_exposure_start_date',
    'procedure_occurrence': 'procedure_date', 'device_exposure': 'device_exposure_start_date',
    'measurement': 'measurement_date', 'observation': 'observation_date', 'specimen': 'specimen_date',
    'note': 'note_date', 'condition_era': 'condition_era_start_date', 'drug_era': 'drug_era_start_date',
    'dose_era': 'dose_era_start_date',
}


def export_parquet(con, schema, outdir, tables=None, row_group_size=122880, log=print):
    """Write schema.<table> (views or tables) to outdir/<table>/[year=Y/]data.parquet, sorted by SORT_KEYS.
    Large event tables are written one year at a time (bounded memory); other tables as a single file."""
    import time
    os.makedirs(outdir, exist_ok=True)
    have = {r[0] for r in con.execute(
        'select table_name from information_schema.tables where table_schema = ?', [schema]).fetchall()}
    for table in (tables or list(cdm_fields())):
        if table not in have:
            continue
        n = con.execute(f'SELECT count(*) FROM {schema}.{table}').fetchone()[0]
        if n == 0:
            continue
        t0 = time.time()
        order = ', '.join(SORT_KEYS.get(table, [])) or '1'
        tdir = os.path.join(outdir, table)
        os.makedirs(tdir, exist_ok=True)
        opts = f"(FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {int(row_group_size)})"
        date = PARTITION_DATE.get(table)
        if date:
            years = [r[0] for r in con.execute(
                f'SELECT DISTINCT year({date}) FROM {schema}.{table} ORDER BY 1').fetchall()]
            for y in years:
                ydir = os.path.join(tdir, f'year={y if y is not None else "__null__"}')
                os.makedirs(ydir, exist_ok=True)
                cond = f'year({date}) = {int(y)}' if y is not None else f'{date} IS NULL'
                con.execute(f"COPY (SELECT * FROM {schema}.{table} WHERE {cond} ORDER BY {order}) "
                            f"TO '{os.path.join(ydir, 'data.parquet')}' {opts}")
        else:
            con.execute(f"COPY (SELECT * FROM {schema}.{table} ORDER BY {order}) "
                        f"TO '{os.path.join(tdir, 'data.parquet')}' {opts}")
        if log:
            log(f'  {table:<24} {n:>14,} rows  {time.time() - t0:7.0f}s')
