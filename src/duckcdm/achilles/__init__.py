"""OHDSI Achilles 의 분석 SQL 을 그대로 duckcdm 번역기로 DuckDB 에서 돌린다.

    from duckcdm.achilles import run_achilles
    run_achilles(con, cdm_schema='main', results_schema='results')

결과: <results>.achilles_results, achilles_results_dist, achilles_analysis, achilles_result_concept_count —
Achilles R 패키지와 같은 테이블이라 WebAPI/ATLAS 의 데이터 소스 보고서 SQL 이 그대로 읽는다.
"""
import csv
import os
import time

from ..sqlrender import render, split_sql, translate

_DIR = os.path.dirname(__file__)
ACHILLES_VERSION = '1.7.2'
PREFIX = 'tmpach'

# WebAPI 데이터 소스 보고서가 읽는 분석 + 개념 기록수(achilles_result_concept_count)에 필요한 분석
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
                 cdm_version='5.3', analysis_ids=None, small_cell_count=5, log=print):
    """분석을 돌려 결과 테이블을 새로 만든다. 돌아가지 않은 분석은 (id, 오류) 목록으로 돌려준다."""
    vocab_schema = vocab_schema or cdm_schema
    details = analysis_details()
    ids = available(analysis_ids)
    con.execute(f'CREATE SCHEMA IF NOT EXISTS {results_schema}')
    params = dict(cdmDatabaseSchema=cdm_schema, scratchDatabaseSchema=results_schema, schemaDelim='.',
                  tempAchillesPrefix=PREFIX, resultsDatabaseSchema=results_schema, vocabDatabaseSchema=vocab_schema,
                  tempEmulationSchema=results_schema, source_name=source_name, achilles_version=ACHILLES_VERSION,
                  cdmVersion=cdm_version, singleThreaded=False)
    done, failed = [], []
    for aid in ids:
        for t in (f'{PREFIX}_{aid}', f'{PREFIX}_dist_{aid}'):
            con.execute(f'DROP TABLE IF EXISTS {results_schema}.{t}')
        t0 = time.time()
        try:
            _run(con, render(_sql(f'{aid}.sql'), **params))
            done.append(aid)
            if log:
                log(f'  achilles {aid:>5} {time.time() - t0:6.2f}s')
        except Exception as e:                       # 분석 하나가 실패해도 나머지는 계속 (Achilles 와 같다)
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

    fields = _schema('schema_achilles_results_concept_count.csv')
    _run(con, render(_sql('create_result_concept_table.sql'), resultsDatabaseSchema=results_schema,
                     vocabDatabaseSchema=vocab_schema, fieldNames=', '.join(n for n, _ in fields)))

    _add_person_counts(con, results_schema, vocab_schema)

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


# 개념별 환자 수 분석(… by concept 의 person 수). WebAPI 의 achilles_result_concept_count 는 PC·DPC 열도 갖는다.
PERSON_COUNT_ANALYSES = (200, 400, 600, 700, 800, 900, 1000, 1800, 2100)


def _add_person_counts(con, results_schema, vocab_schema):
    """record_count·descendant_record_count 와 같은 방식(자신=최댓값, 자손=합)으로 person 수를 붙인다."""
    ids = ','.join(map(str, PERSON_COUNT_ANALYSES))
    rs, vs = results_schema, vocab_schema
    con.execute(f"""
        CREATE OR REPLACE TABLE {rs}.achilles_result_concept_count AS
        WITH pc AS (
            SELECT stratum_1 AS concept_id, MAX(count_value) AS cnt FROM {rs}.achilles_results
            WHERE analysis_id IN ({ids}) GROUP BY stratum_1),
        concepts AS (
            SELECT CAST(ancestor_concept_id AS VARCHAR) a, CAST(descendant_concept_id AS VARCHAR) d
            FROM {vs}.concept_ancestor
            UNION SELECT CAST(concept_id AS VARCHAR), CAST(concept_id AS VARCHAR) FROM {vs}.concept),
        agg AS (
            SELECT c.a AS concept_id, COALESCE(MAX(p1.cnt), 0) AS person_count,
                   COALESCE(SUM(p2.cnt), 0) AS descendant_person_count
            FROM concepts c LEFT JOIN pc p1 ON c.a = p1.concept_id LEFT JOIN pc p2 ON c.d = p2.concept_id
            GROUP BY c.a)
        SELECT r.concept_id, r.record_count, r.descendant_record_count,
               CAST(COALESCE(a.person_count, 0) AS BIGINT) AS person_count,
               CAST(COALESCE(a.descendant_person_count, 0) AS BIGINT) AS descendant_person_count
        FROM {rs}.achilles_result_concept_count r LEFT JOIN agg a ON CAST(r.concept_id AS VARCHAR) = a.concept_id""")
