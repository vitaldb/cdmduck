"""PhenotypeLibrary 코호트를 cdmduck 으로 SQL 생성 → DuckDB 방언 번역 → 실행.

python3 run_phenotypes.py <cdm.duckdb (복사본)> <cohorts dir> [개수]
"""
import glob, os, sys, threading, time

import duckdb

from cdmduck.circe import build_cohort_query
from cdmduck.sqlrender import render, split_sql, translate

db, cdir = sys.argv[1], sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else None
files = sorted(glob.glob(os.path.join(cdir, '*.json')), key=lambda f: int(os.path.basename(f).split('.')[0]))[:limit]
ok, fail, persons, slow = 0, [], {}, []
TIMEOUT = float(os.environ.get('COHORT_TIMEOUT', '30'))
t0 = time.time()
for f in files:
    cid = int(os.path.basename(f).split('.')[0])
    con = duckdb.connect(db)          # 코호트마다 새 세션(임시 테이블 격리) — WebAPI 와 같다
    timer = threading.Timer(TIMEOUT, con.interrupt)
    timer.start()
    t1 = time.time()
    try:
        sql = build_cohort_query(open(f, encoding='utf-8').read(), cdm_schema='main', target_table='main.cohort',
                                 result_schema='main', vocabulary_schema='main', cohort_id=cid, generate_stats=False)
        t_build = time.time() - t1
        sql = translate(render(sql), 'duckdb')
        t_tr = time.time() - t1 - t_build
        for stmt in split_sql(sql):
            if stmt.strip():
                con.execute(stmt)
        persons[cid] = con.execute('select count(distinct subject_id) from main.cohort where cohort_definition_id = ?',
                                   [cid]).fetchone()[0]
        ok += 1
    except Exception as e:
        fail.append((cid, type(e).__name__, str(e).splitlines()[0][:200]))
    finally:
        timer.cancel()
        dt = time.time() - t1
        if dt > 5:
            slow.append((cid, round(dt, 1)))
        print(cid, round(dt, 2), 'build/translate', round(locals().get('t_build', 0), 2),
              round(locals().get('t_tr', 0), 2), 'len', len(locals().get('sql', '')), flush=True)
        con.close()
print(f'ok {ok} / {len(files)}  fail {len(fail)}  {time.time() - t0:.1f}s')
print('cohorts with persons:', sum(1 for v in persons.values() if v), 'max', max(persons.values(), default=0))
for x in fail[:30]:
    print('FAIL', x)
print('slow(>5s):', slow)
