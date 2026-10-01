"""Run OHDSI Achilles analysis SQL unchanged on DuckDB via the duckcdm translator.

    from duckcdm.achilles import run_achilles
    run_achilles(con, cdm_schema='main', results_schema='results')

Results: <results>.achilles_results, achilles_results_dist, achilles_analysis, achilles_result_concept_count —
the same tables as the Achilles R package, so WebAPI/ATLAS data source report SQL reads them as is.
"""
import csv
import os
import time

from ..sqlrender import render, split_sql, translate

_DIR = os.path.dirname(__file__)
ACHILLES_VERSION = '1.7.2'
PREFIX = 'tmpach'

# Analyses read by WebAPI data source reports + analyses needed for concept record counts (achilles_result_concept_count)
REPORT_ANALYSES = [
    0, 1, 2, 3, 4, 5, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 113, 116, 117,
    200, 201, 202, 203, 204, 206, 211, 220, 301, 400, 401, 402, 403, 404, 405, 406, 420,
    501, 502, 504, 505, 506, 600, 601, 602, 603, 604, 605, 606, 620, 691, 700, 701, 702, 703, 704, 705, 706,
    715, 716, 717, 720, 791, 800, 801, 802, 803, 804, 805, 806, 807, 820, 822, 823, 891,
    900, 901, 902, 903, 904, 906, 907, 920, 1000, 1001, 1002, 1003, 1004, 1006, 1007, 1020, 1201,
    1800, 1801, 1802, 1803, 1804, 1805, 1806, 1807, 1815, 1816, 1817, 1818, 1820, 1822, 1823, 1891,
    2101, 2105,
]


def analysis_details():
    with open(os.path.join(_DIR, 'csv', 'achilles_analysis_details.csv'), encoding='utf-8') as f:
        return {int(r['analysis_id']): r for r in csv.DictReader(f)}


def _schema(name):
    with open(os.path.join(_DIR, 'csv', name), encoding='utf-8') as f:
        return [(r['field_name'], r['field_type']) for r in csv.DictReader(f)]


def _sql(name):
    with open(os.path.join(_DIR, 'sql', name), encoding='utf-8') as f:
        return f.read()


def _run(con, ohdsi_sql):
    for stmt in split_sql(translate(ohdsi_sql, 'duckdb')):
        if stmt.strip():
            con.execute(stmt)


def available(ids=None):
    have = {int(n[:-4]) for n in os.listdir(os.path.join(_DIR, 'sql')) if n[:-4].isdigit()}
    return [i for i in (ids or REPORT_ANALYSES) if i in have]


def run_achilles(con, cdm_schema='main', results_schema='results', vocab_schema=None, source_name='duckcdm',
                 cdm_version='5.3', analysis_ids=None, small_cell_count=5, log=lambda m: print(m, flush=True),
                 resume=False, skip=()):
    """Run the analyses and rebuild the result tables. Returns failed analyses as a list of (id, error)."""
    vocab_schema = vocab_schema or cdm_schema
    details = analysis_details()
    ids = available(analysis_ids)
    con.execute(f'CREATE SCHEMA IF NOT EXISTS {results_schema}')
    params = dict(cdmDatabaseSchema=cdm_schema, scratchDatabaseSchema=results_schema, schemaDelim='.',
                  tempAchillesPrefix=PREFIX, resultsDatabaseSchema=results_schema, vocabDatabaseSchema=vocab_schema,
                  tempEmulationSchema=results_schema, source_name=source_name, achilles_version=ACHILLES_VERSION,
                  cdmVersion=cdm_version, singleThreaded=False)
    done, failed = [], []
    have = {r[0] for r in con.execute(
        "select table_name from information_schema.tables where table_schema = ?", [results_schema]).fetchall()}
    for aid in ids:
        if aid in skip:
            if log:
                log(f'  achilles {aid:>5} skipped')
            continue
        if resume and (f'{PREFIX}_{aid}' in have or f'{PREFIX}_dist_{aid}' in have):
            done.append(aid)            # scratch table from an earlier, interrupted run
            continue
        for t in (f'{PREFIX}_{aid}', f'{PREFIX}_dist_{aid}'):
            con.execute(f'DROP TABLE IF EXISTS {results_schema}.{t}')
        t0 = time.time()
        try:
            _run(con, render(_sql(f'{aid}.sql'), **params))
            done.append(aid)
            if log:
                log(f'  achilles {aid:>5} {time.time() - t0:6.2f}s')
        except Exception as e:                       # keep going if one analysis fails (same as Achilles)
            failed.append((aid, f'{type(e).__name__}: {str(e).splitlines()[0][:200]}'))
            if log:
                log(f'  achilles {aid:>5} FAILED {failed[-1][1]}')

    existing = {r[0] for r in con.execute(
        "select table_name from information_schema.tables where table_schema = ?", [results_schema]).fetchall()}
    for detail_type, schema_file, dist_flags, prefix in (
            ('results', 'schema_achilles_results.csv', ('0', '-1'), PREFIX),
            ('results_dist', 'schema_achilles_results_dist.csv', ('1', '-1'), PREFIX + '_dist')):
        fields = _schema(schema_file)
        casted = ', '.join(f'cast({n} as {t}) as {n}' for n, t in fields)
        selects = [f'select {casted} from {results_schema}.{prefix}_{a}' for a in done
                   if details.get(a, {}).get('distribution') in dist_flags and f'{prefix}_{a}' in existing]
        if not selects:
            selects = [f'select {casted} from (select ' + ', '.join(f'cast(null as {t}) as {n}' for n, t in fields)
                       + ') z where 1=0']
        sql = render(_sql('merge_achilles_tables.sql'), createTable=True, resultsDatabaseSchema=results_schema,
                     detailType=detail_type, detailSqls=' \nunion all\n '.join(selects),
                     fieldNames=', '.join(n for n, _ in fields),
                     smallCellCount=small_cell_count if small_cell_count is not None else '')
        _run(con, sql)

    build_concept_counts(con, results_schema, vocab_schema)

    con.execute(f'DROP TABLE IF EXISTS {results_schema}.achilles_analysis')
    con.execute(f'CREATE TABLE {results_schema}.achilles_analysis (analysis_id INTEGER, analysis_name VARCHAR, '
                'stratum_1_name VARCHAR, stratum_2_name VARCHAR, stratum_3_name VARCHAR, stratum_4_name VARCHAR, '
                'stratum_5_name VARCHAR, is_default INTEGER, category VARCHAR)')
    con.executemany(f'INSERT INTO {results_schema}.achilles_analysis VALUES (?,?,?,?,?,?,?,?,?)', [
        (int(r['analysis_id']), r['analysis_name'], r['stratum_1_name'] or None, r['stratum_2_name'] or None,
         r['stratum_3_name'] or None, r['stratum_4_name'] or None, r['stratum_5_name'] or None,
         int(r['is_default'] or 0), r['category']) for r in details.values()])

    for aid in done:
        for t in (f'{PREFIX}_{aid}', f'{PREFIX}_dist_{aid}'):
            con.execute(f'DROP TABLE IF EXISTS {results_schema}.{t}')
    return done, failed


# Analyses that feed record counts (same lists as Achilles' create_result_concept_table.sql)
RC_MAX_ANALYSES = (2, 4, 5, 201, 225, 301, 325, 401, 425, 501, 505, 525, 601, 625, 701, 725, 801, 825, 826, 827, 901,
                   1001, 1201, 1425, 1801, 1825, 1826, 1827, 2101, 2125, 2301)
RC_SUM_ANALYSES = (405, 605, 705, 805, 807, 1805, 1807, 2105)
# Per-concept person count analyses; WebAPI's achilles_result_concept_count also has PC and DPC columns
PERSON_COUNT_ANALYSES = (200, 400, 600, 700, 800, 900, 1000, 1800, 2100)


def build_concept_counts(con, results_schema, vocab_schema):
    """achilles_result_concept_count with the same values as Achilles' create_result_concept_table.sql
    (record_count = MAX over the concept's own counts, descendant_record_count = SUM over its descendants
    including itself), plus person_count / descendant_person_count computed the same way.

    Achilles joins every (ancestor, descendant) pair of the whole vocabulary before aggregating, which needs
    tens of GB on a full vocabulary. Here only pairs whose descendant has a count are joined. One quirk of the
    original is kept: a concept that appears in both count lists gets its descendant sum counted once per list.
    """
    rs, vs = results_schema, vocab_schema
    rc_max = ','.join(map(str, RC_MAX_ANALYSES))
    rc_sum = ','.join(map(str, RC_SUM_ANALYSES))
    pc_ids = ','.join(map(str, PERSON_COUNT_ANALYSES))
    con.execute(f"""
        CREATE OR REPLACE TABLE {rs}.achilles_result_concept_count AS
        WITH counts AS (
            SELECT stratum_1 AS concept_id, MAX(count_value) AS cnt FROM {rs}.achilles_results
            WHERE analysis_id IN ({rc_max}) GROUP BY stratum_1
            UNION
            SELECT stratum_2 AS concept_id, SUM(count_value) AS cnt FROM {rs}.achilles_results
            WHERE analysis_id IN ({rc_sum}) GROUP BY stratum_2),
        pcounts AS (
            SELECT stratum_1 AS concept_id, MAX(count_value) AS cnt FROM {rs}.achilles_results
            WHERE analysis_id IN ({pc_ids}) GROUP BY stratum_1),
        counted AS (SELECT concept_id FROM counts UNION SELECT concept_id FROM pcounts),
        pairs AS (
            SELECT DISTINCT CAST(ca.ancestor_concept_id AS VARCHAR) AS a, CAST(ca.descendant_concept_id AS VARCHAR) AS d
            FROM {vs}.concept_ancestor ca
            WHERE CAST(ca.descendant_concept_id AS VARCHAR) IN (SELECT concept_id FROM counted)
            UNION
            SELECT concept_id, concept_id FROM counted),
        own AS (SELECT concept_id, MAX(cnt) AS rc, COUNT(*) AS mult FROM counts GROUP BY concept_id),
        own_p AS (SELECT concept_id, MAX(cnt) AS pc FROM pcounts GROUP BY concept_id),
        drc AS (SELECT p.a, SUM(c.cnt) AS s FROM pairs p JOIN counts c ON p.d = c.concept_id GROUP BY p.a),
        dpc AS (SELECT p.a, SUM(c.cnt) AS s FROM pairs p JOIN pcounts c ON p.d = c.concept_id GROUP BY p.a),
        ids AS (
            SELECT CAST(ancestor_concept_id AS VARCHAR) AS concept_id FROM {vs}.concept_ancestor
            UNION SELECT CAST(concept_id AS VARCHAR) FROM {vs}.concept)
        SELECT ids.concept_id,
               COALESCE(own.rc, 0) AS record_count,
               COALESCE(drc.s, 0) * COALESCE(own.mult, 1) AS descendant_record_count,
               CAST(COALESCE(own_p.pc, 0) AS BIGINT) AS person_count,
               CAST(COALESCE(dpc.s, 0) AS BIGINT) AS descendant_person_count
        FROM ids
        LEFT JOIN own ON own.concept_id = ids.concept_id
        LEFT JOIN own_p ON own_p.concept_id = ids.concept_id
        LEFT JOIN drc ON drc.a = ids.concept_id
        LEFT JOIN dpc ON dpc.a = ids.concept_id""")
