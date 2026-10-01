"""ATLAS 3.0 화면이 부르는 WebAPI 의 부분집합 — FastAPI + DuckDB.

경로는 WebAPI 와 같다(/WebAPI/... 아래). 화면(ATLAS 3.0 빌드)은 / 에서 함께 낸다.
"""
import json
import os
import threading
import time
import traceback

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..circe import CirceError, build_cohort_query, build_concept_set_query
from ..sqlrender import render, translate
from .sources import Source, like_text, q, q_in, sql_resource
from .store import Store, now_ms

STATIC = os.path.join(os.path.dirname(__file__), 'static', 'atlas')
STANDARD_CAPTION = {'N': 'Non-Standard', 'S': 'Standard', 'C': 'Classification'}
INVALID_CAPTION = {'V': 'Valid', 'D': 'Invalid', 'U': 'Invalid'}


def _ms(d):
    if d is None:
        return None
    if hasattr(d, 'timestamp'):
        return int(d.timestamp() * 1000)
    import datetime
    return int(time.mktime(datetime.datetime(d.year, d.month, d.day).timetuple()) * 1000)


def concept_json(r):
    """어휘 SQL 의 한 행 → WebAPI Concept JSON (대문자 키)."""
    g = lambda k: r.get(k, r.get(k.lower()))
    sc, ir = g('STANDARD_CONCEPT'), g('INVALID_REASON')
    out = {
        'CONCEPT_ID': g('CONCEPT_ID'), 'CONCEPT_NAME': g('CONCEPT_NAME'),
        'STANDARD_CONCEPT': sc, 'STANDARD_CONCEPT_CAPTION': STANDARD_CAPTION.get(sc, 'Unknown'),
        'INVALID_REASON': ir, 'INVALID_REASON_CAPTION': INVALID_CAPTION.get(ir, 'Unknown'),
        'CONCEPT_CODE': g('CONCEPT_CODE'), 'DOMAIN_ID': g('DOMAIN_ID'), 'VOCABULARY_ID': g('VOCABULARY_ID'),
        'CONCEPT_CLASS_ID': g('CONCEPT_CLASS_ID'),
        'VALID_START_DATE': _ms(g('VALID_START_DATE')), 'VALID_END_DATE': _ms(g('VALID_END_DATE')),
    }
    if g('RELATIONSHIP_NAME') is not None:
        out['RELATIONSHIPS'] = [{'RELATIONSHIP_NAME': g('RELATIONSHIP_NAME'),
                                 'RELATIONSHIP_DISTANCE': g('RELATIONSHIP_DISTANCE')}]
    return out


def _merge_related(rows):
    """WebAPI 처럼 같은 개념의 관계를 RELATIONSHIPS 목록으로 합친다."""
    out, by_id = [], {}
    for r in rows:
        c = concept_json(r)
        k = c['CONCEPT_ID']
        if k in by_id:
            by_id[k]['RELATIONSHIPS'].extend(c.get('RELATIONSHIPS', []))
        else:
            by_id[k] = c
            out.append(c)
    return out


def create_app(sources, store, title='cdmduck'):
    app = FastAPI(title=title, version=__version__, docs_url='/WebAPI/docs', openapi_url='/WebAPI/openapi.json')
    by_key = {s.key: s for s in sources}
    by_id = {s.source_id: s for s in sources}

    def src(key):
        s = by_key.get(key)
        if s is None:
            raise HTTPException(404, f'source {key} not found')
        return s

    @app.exception_handler(CirceError)
    async def _circe_err(request, exc):
        return JSONResponse({'message': str(exc)}, status_code=400)

    # ---- 기본 정보 ------------------------------------------------------------
    @app.get('/WebAPI/info')
    def info():
        return {'version': '3.0.0', 'buildInfo': {'artifactVersion': 'cdmduck ' + __version__, 'branch': 'main',
                                                  'commitId': '', 'buildTimestamp': ''},
                'configuration': {'security': {'enabled': False}, 'vocabulary': {'solr': {'enabled': False}},
                                  'person': {'viewDatesPermitted': True}, 'plp': {'enabled': False},
                                  'heracles': {'smallCellCount': 5}, 'cdmduck': {'version': __version__}}}

    @app.get('/WebAPI/user/me')
    def user_me():
        return {'user': store.user, 'authz': {'permissions': ['*'], 'cohortDefinitionAccess': {},
                                              'conceptSetAccess': {}, 'sourceAccess': {}}}

    @app.get('/WebAPI/permission/')
    @app.get('/WebAPI/permission')
    def permissions():
        return []

    @app.get('/WebAPI/role/')
    @app.get('/WebAPI/role')
    def roles():
        return [{'id': 1, 'name': 'admin', 'systemRole': True}]

    @app.get('/WebAPI/user/')
    @app.get('/WebAPI/user')
    def users():
        return [store.user]

    @app.get('/WebAPI/permission/access/{entity}/{eid}')
    def access(entity, eid):
        return []

    # ---- 소스 -----------------------------------------------------------------
    @app.get('/WebAPI/source/sources')
    def list_sources():
        return [s.info() for s in sources]

    @app.get('/WebAPI/source/{key}')
    def source_details(key):
        return src(key).details()

    @app.get('/WebAPI/source/{key}/connectionCheck')
    @app.get('/WebAPI/source/connection/{key}')
    def source_check(key):
        s = src(key)
        s.cursor().execute(f'SELECT 1 FROM {s.cdm_schema}.person LIMIT 1')
        return {'sourceKey': key, 'connected': True}

    @app.post('/WebAPI/source/{key}/refreshSourceCache')
    @app.get('/WebAPI/cdmresults/{key}/refreshCache')
    @app.post('/WebAPI/cdmresults/{key}/clearCache')
    @app.post('/WebAPI/cache/clear')
    def noop_cache(key=None):
        return {}

    # ---- 어휘 -----------------------------------------------------------------
    def vocab(key, name, **params):
        s = src(key)
        return s.query_dicts(sql_resource('vocabulary', name), CDM_schema=s.vocab_schema, **params)

    @app.get('/WebAPI/vocabulary/{key}/info')
    def vocab_info(key):
        rows = vocab(key, 'getInfo.sql')
        return {'version': rows[0]['VOCABULARY_VERSION'] if rows else 'unknown', 'dialect': 'duckdb'}

    @app.get('/WebAPI/vocabulary/{key}/domains')
    def domains(key):
        return [{'DOMAIN_ID': r['DOMAIN_ID'], 'DOMAIN_NAME': r['DOMAIN_NAME'], 'DOMAIN_CONCEPT_ID': r['DOMAIN_CONCEPT_ID']}
                for r in vocab(key, 'getDomains.sql')]

    @app.get('/WebAPI/vocabulary/{key}/vocabularies')
    def vocabularies(key):
        return [{'VOCABULARY_ID': r['VOCABULARY_ID'], 'VOCABULARY_NAME': r['VOCABULARY_NAME'],
                 'VOCABULARY_REFERENCE': r.get('VOCABULARY_REFERENCE'), 'VOCABULARY_VERSION': r.get('VOCABULARY_VERSION'),
                 'VOCABULARY_CONCEPT_ID': r.get('VOCABULARY_CONCEPT_ID')} for r in vocab(key, 'getVocabularies.sql')]

    def search_filters(body):
        f = ''
        if body.get('DOMAIN_ID'):
            f += ' AND DOMAIN_ID IN (%s)' % q_in(body['DOMAIN_ID'])
        if body.get('VOCABULARY_ID'):
            f += ' AND VOCABULARY_ID IN (%s)' % q_in(body['VOCABULARY_ID'])
        if body.get('CONCEPT_CLASS_ID'):
            f += ' AND CONCEPT_CLASS_ID IN (%s)' % q_in(body['CONCEPT_CLASS_ID'])
        ir = (body.get('INVALID_REASON') or '').strip()
        if ir:
            f += ' AND INVALID_REASON IS NULL ' if ir == 'V' else ' AND INVALID_REASON = %s' % q(ir)
        sc = body.get('STANDARD_CONCEPT')
        if sc is not None:
            f += ' AND STANDARD_CONCEPT IS NULL ' if sc == 'N' else ' AND STANDARD_CONCEPT = %s' % q(sc.strip())
        query = (body.get('QUERY') or '').replace('[', '[[]')
        if query:
            lo = like_text(query.lower())
            qf = "LOWER(CONCEPT_NAME) LIKE '%%%s%%' or LOWER(CONCEPT_CODE) LIKE '%%%s%%'" % (lo, lo)
            if query.isdigit():
                qf += ' or CONCEPT_ID = CAST(%s as int)' % q(query)
            f += ' AND (' + qf + ')'
        return f

    @app.post('/WebAPI/vocabulary/{key}/search')
    async def search(key, request: Request):
        body = await request.json()
        s = src(key)
        sql = sql_resource('vocabulary', 'search.sql').replace('@filters', search_filters(body))
        rows = s.query_dicts(sql + '\nLIMIT 20000', CDM_schema=s.vocab_schema)
        return [concept_json(r) for r in rows]

    @app.get('/WebAPI/vocabulary/{key}/search')
    def search_get(key, query: str = ''):
        s = src(key)
        sql = sql_resource('vocabulary', 'search.sql').replace('@filters', search_filters({'QUERY': query}))
        return [concept_json(r) for r in s.query_dicts(sql + '\nLIMIT 20000', CDM_schema=s.vocab_schema)]

    @app.get('/WebAPI/vocabulary/{key}/concept/{cid}')
    def concept(key, cid: int):
        rows = vocab(key, 'getConcept.sql', id=cid)
        if not rows:
            raise HTTPException(404, 'concept not found')
        return concept_json(rows[0])

    @app.get('/WebAPI/vocabulary/{key}/concept/{cid}/related')
    def related(key, cid: int):
        return _merge_related(vocab(key, 'getRelatedConcepts.sql', id=cid))

    @app.get('/WebAPI/vocabulary/{key}/concept/{cid}/ancestorAndDescendant')
    def ancestor_descendant(key, cid: int):
        return _merge_related(vocab(key, 'getConceptAncestorAndDescendant.sql', id=cid))

    @app.get('/WebAPI/vocabulary/{key}/concept/{cid}/descendants')
    def descendants(key, cid: int):
        return _merge_related(vocab(key, 'getDescendantConcepts.sql', id=cid))

    @app.post('/WebAPI/vocabulary/{key}/lookup/identifiers')
    async def lookup_identifiers(key, request: Request):
        ids = [int(x) for x in await request.json()]
        if not ids:
            return []
        return [concept_json(r) for r in vocab(key, 'lookupIdentifiers.sql', identifiers=ids)]

    @app.post('/WebAPI/vocabulary/{key}/lookup/sourcecodes')
    async def lookup_sourcecodes(key, request: Request):
        codes = [str(x) for x in await request.json()]
        if not codes:
            return []
        return [concept_json(r) for r in vocab(key, 'lookupSourcecodes.sql', sourcecodes=q_in(codes))]

    @app.post('/WebAPI/vocabulary/{key}/lookup/mapped')
    async def lookup_mapped(key, request: Request):
        ids = [int(x) for x in await request.json()]
        if not ids:
            return []
        return [concept_json(r) for r in vocab(key, 'getMappedSourcecodes.sql', identifiers=ids)]

    @app.post('/WebAPI/vocabulary/{key}/lookup/recommended')
    async def lookup_recommended(key, request: Request):
        return []

    def resolve_expression(s, expression):
        """개념집합 식 → 포함되는 개념들 (vocabulary SQL 로)."""
        inner = build_concept_set_query(expression)
        sql = ('select CONCEPT_ID, CONCEPT_NAME, ISNULL(STANDARD_CONCEPT,\'N\') STANDARD_CONCEPT, '
               'ISNULL(INVALID_REASON,\'V\') INVALID_REASON, CONCEPT_CODE, CONCEPT_CLASS_ID, DOMAIN_ID, VOCABULARY_ID, '
               'VALID_START_DATE, VALID_END_DATE from @vocabulary_database_schema.concept '
               'where concept_id in (%s) order by CONCEPT_NAME' % inner)
        return s.query_dicts(sql, vocabulary_database_schema=s.vocab_schema)

    @app.post('/WebAPI/vocabulary/{key}/resolveConceptSetExpression')
    async def resolve(key, request: Request):
        return [r['CONCEPT_ID'] for r in resolve_expression(src(key), await request.json())]

    @app.post('/WebAPI/vocabulary/{key}/included-concepts/count')
    async def included_count(key, request: Request):
        return len(resolve_expression(src(key), await request.json()))

    @app.post('/WebAPI/vocabulary/conceptSetExpressionSQL')
    async def concept_set_sql(request: Request):
        return PlainTextResponse(build_concept_set_query(await request.json()))

    @app.post('/WebAPI/cdmresults/{key}/conceptRecordCount')
    async def concept_record_count(key, request: Request):
        return []                       # Achilles 결과가 없으므로 비움 (화면은 RC/DRC 를 비워 보여 준다)

    # ---- 개념집합 ---------------------------------------------------------------
    @app.get('/WebAPI/conceptset')
    @app.get('/WebAPI/conceptset/')
    def list_concept_sets():
        return store.list_concept_sets()

    @app.get('/WebAPI/conceptset/{cid}')
    def get_concept_set(cid: int):
        cs = store.get_concept_set(cid)
        if cs is None:
            raise HTTPException(404, 'concept set not found')
        return cs

    @app.post('/WebAPI/conceptset')
    @app.post('/WebAPI/conceptset/')
    async def create_concept_set(request: Request):
        b = await request.json()
        return store.create_concept_set(b.get('name') or 'New Concept Set', b.get('description'))

    @app.put('/WebAPI/conceptset/{cid}')
    async def update_concept_set(cid: int, request: Request):
        b = await request.json()
        get_concept_set(cid)
        return store.update_concept_set(cid, b.get('name'), b.get('description'))

    @app.delete('/WebAPI/conceptset/{cid}')
    def delete_concept_set(cid: int):
        store.delete_concept_set(cid)
        return Response(status_code=204)

    @app.get('/WebAPI/conceptset/{cid}/items')
    def concept_set_items(cid: int):
        get_concept_set(cid)
        return store.get_items(cid)

    @app.put('/WebAPI/conceptset/{cid}/items')
    async def put_items(cid: int, request: Request):
        get_concept_set(cid)
        store.set_items(cid, await request.json())
        return store.get_items(cid)

    def expression_of(cid, s):
        items = store.get_items(cid)
        ids = [i['conceptId'] for i in items]
        concepts = {r['CONCEPT_ID']: concept_json(r) for r in vocab(s.key, 'lookupIdentifiers.sql', identifiers=ids)} \
            if ids else {}
        missing = [i for i in ids if i not in concepts]
        if missing:
            raise HTTPException(400, f'Vocabulary {s.key} does not contain required concepts: {missing}')
        return {'items': [{'concept': concepts[i['conceptId']], 'isExcluded': bool(i['isExcluded']),
                           'includeDescendants': bool(i['includeDescendants']), 'includeMapped': bool(i['includeMapped'])}
                          for i in items]}

    @app.get('/WebAPI/conceptset/{cid}/expression')
    def concept_set_expression(cid: int):
        get_concept_set(cid)
        return expression_of(cid, sources[0])

    @app.get('/WebAPI/conceptset/{cid}/expression/{key}')
    def concept_set_expression_src(cid: int, key):
        get_concept_set(cid)
        return expression_of(cid, src(key))

    @app.get('/WebAPI/conceptset/{cid}/exists')
    def concept_set_exists(cid: int, name: str = ''):
        return sum(1 for c in store.list_concept_sets() if c['name'] == name and c['id'] != cid)

    @app.get('/WebAPI/conceptset/{cid}/generationinfo')
    def concept_set_geninfo(cid: int):
        return []

    @app.post('/WebAPI/conceptset/{cid}/tag/')
    @app.post('/WebAPI/conceptset/{cid}/tag')
    @app.delete('/WebAPI/conceptset/{cid}/tag/{tag}')
    def concept_set_tag(cid: int, tag: int = 0):
        return {}

    # ---- 코호트 정의 -------------------------------------------------------------
    def expression_text(b):
        e = b.get('expression')
        return e if isinstance(e, str) else json.dumps(e)

    @app.get('/WebAPI/cohortdefinition')
    @app.get('/WebAPI/cohortdefinition/')
    def list_cohorts():
        return store.list_cohorts()

    @app.get('/WebAPI/cohortdefinition/{cid}')
    def get_cohort(cid: int):
        c = store.get_cohort(cid)
        if c is None:
            raise HTTPException(404, 'cohort definition not found')
        return c

    @app.post('/WebAPI/cohortdefinition')
    @app.post('/WebAPI/cohortdefinition/')
    async def create_cohort(request: Request):
        b = await request.json()
        return store.create_cohort(b.get('name') or 'New Cohort Definition', b.get('description'),
                                   b.get('expressionType'), expression_text(b))

    @app.put('/WebAPI/cohortdefinition/{cid}')
    async def update_cohort(cid: int, request: Request):
        b = await request.json()
        get_cohort(cid)
        return store.update_cohort(cid, b.get('name'), b.get('description'), b.get('expressionType'),
                                   expression_text(b))

    @app.delete('/WebAPI/cohortdefinition/{cid}')
    def delete_cohort(cid: int):
        store.delete_cohort(cid)
        return Response(status_code=204)

    @app.get('/WebAPI/cohortdefinition/{cid}/exists')
    def cohort_exists(cid: int, name: str = ''):
        return sum(1 for c in store.list_cohorts() if c['name'] == name and c['id'] != cid)

    @app.get('/WebAPI/cohortdefinition/{cid}/copy')
    def copy_cohort(cid: int):
        c = get_cohort(cid)
        return store.create_cohort('COPY OF: ' + c['name'], c['description'], c['expressionType'], c['expression'])

    @app.post('/WebAPI/cohortdefinition/{cid}/tag/')
    @app.post('/WebAPI/cohortdefinition/{cid}/tag')
    @app.delete('/WebAPI/cohortdefinition/{cid}/tag/{tag}')
    def cohort_tag(cid: int, tag: int = 0):
        return {}

    @app.post('/WebAPI/cohortdefinition/sql')
    async def cohort_sql(request: Request):
        b = await request.json()
        opts = b.get('options')
        return {'templateSql': build_cohort_query(b['expression'], opts)}

    @app.post('/WebAPI/cohortdefinition/check')
    @app.post('/WebAPI/cohortdefinition/checkV2')
    async def cohort_check(request: Request):
        b = await request.json()
        warnings = []
        try:
            build_cohort_query(b.get('expression') or {})
        except CirceError as e:
            warnings.append({'type': 'DefaultWarning', 'severity': 'CRITICAL', 'message': str(e)})
        return {'warnings': warnings}

    @app.post('/WebAPI/sqlrender/translate')
    async def sqlrender_translate(request: Request):
        b = await request.json()
        sql = render(b.get('sql') or '', **(b.get('parameters') or {}))
        return {'targetSQL': translate(sql, b.get('targetDialect') or 'duckdb',
                                       temp_emulation_schema=b.get('oracleTempSchema'))}

    # ---- 코호트 생성 -------------------------------------------------------------
    def run_generation(cid, s, jid):
        t0 = time.time()
        c = store.get_cohort(cid)
        try:
            expr = json.loads(c['expression'])
            sql = build_cohort_query(expr, cdm_schema=s.cdm_schema, vocabulary_schema=s.vocab_schema,
                                     result_schema=s.results_schema, target_table=s.results_schema + '.cohort',
                                     cohort_id=cid, generate_stats=True)
            with s.lock:
                cur = s.cursor()
                cur.execute(f'DELETE FROM {s.results_schema}.cohort_inclusion WHERE cohort_definition_id = ?', [cid])
                for i, rule in enumerate(expr.get('InclusionRules') or []):
                    cur.execute(f'INSERT INTO {s.results_schema}.cohort_inclusion VALUES (?, NULL, ?, ?, ?)',
                                [cid, i, (rule.get('name') or '')[:255], (rule.get('description') or '')[:1000]])
                s.execute_script(render(sql), cur)
                pc, rc = cur.execute(f'SELECT count(distinct subject_id), count(*) FROM {s.results_schema}.cohort '
                                     'WHERE cohort_definition_id = ?', [cid]).fetchone()
            store.set_generation(cid, s.source_id, status='COMPLETE', is_valid=True,
                                 execution_duration=int((time.time() - t0) * 1000), person_count=pc, record_count=rc,
                                 fail_message=None)
            store.finish_job(jid, 'COMPLETED', 'COMPLETED')
        except Exception as e:
            traceback.print_exc()
            store.set_generation(cid, s.source_id, status='FAILED', is_valid=False,
                                 execution_duration=int((time.time() - t0) * 1000),
                                 fail_message=f'{type(e).__name__}: {e}'[:4000])
            store.finish_job(jid, 'FAILED', f'FAILED: {e}'[:1000])

    @app.get('/WebAPI/cohortdefinition/{cid}/generate/{key}')
    def generate(cid: int, key):
        get_cohort(cid)
        s = src(key)
        jid = store.start_job('generateCohort', {'cohort_definition_id': cid, 'source_id': s.source_id,
                                                 'source_key': key, 'jobName': f'generating cohort {cid} : {key}',
                                                 'jobAuthor': store.user['login']})
        store.set_generation(cid, s.source_id, start_time=now_ms(), status='RUNNING', is_valid=False,
                             is_canceled=False, fail_message=None, person_count=None, record_count=None)
        threading.Thread(target=run_generation, args=(cid, s, jid), daemon=True).start()
        return store.job(jid)

    @app.get('/WebAPI/cohortdefinition/{cid}/cancel/{key}')
    def cancel(cid: int, key):
        return {}

    @app.get('/WebAPI/cohortdefinition/{cid}/info')
    def generation_info(cid: int):
        get_cohort(cid)
        return store.generation_info(cid)

    @app.get('/WebAPI/cohortdefinition/{cid}/report/{key}/inclusion')
    @app.get('/WebAPI/cohortdefinition/{cid}/report/{key}')
    def inclusion_report(cid: int, key, mode: int = 0):
        s = src(key)
        cur = s.cursor()
        rs = s.results_schema
        row = cur.execute(f'select cs.base_count, cs.final_count, coalesce(cc.lost_count, 0) from {rs}.cohort_summary_stats cs '
                          f'left join {rs}.cohort_censor_stats cc on cc.cohort_definition_id = cs.cohort_definition_id '
                          'where cs.cohort_definition_id = ? and cs.mode_id = ?', [cid, mode]).fetchone()
        base, final, lost = row if row else (0, 0, 0)
        pct = lambda n, d: '%.2f%%' % (100.0 * n / d) if d else '0.00%'
        stats = []
        for seq, name, pcount, gain, total in cur.execute(
                f'select i.rule_sequence, i.name, s.person_count, s.gain_count, s.person_total from {rs}.cohort_inclusion i '
                f'join {rs}.cohort_inclusion_stats s on i.cohort_definition_id = s.cohort_definition_id and '
                'i.rule_sequence = s.rule_sequence where i.cohort_definition_id = ? and mode_id = ? '
                'order by i.rule_sequence', [cid, mode]).fetchall():
            stats.append({'id': seq, 'name': name, 'countSatisfying': pcount,
                          'percentSatisfying': pct(pcount, total), 'percentExcluded': pct(total - pcount, total)})
        groups = {}
        for mask, n in cur.execute(f'select inclusion_rule_mask, person_count from {rs}.cohort_inclusion_result '
                                   'where cohort_definition_id = ? and mode_id = ?', [cid, mode]).fetchall():
            groups.setdefault(bin(mask).count('1'), []).append((mask, n))
        nrules = len(stats)
        children = [{'name': 'Group %d' % k, 'children': [
            {'name': ''.join('1' if m >> i & 1 else '0' for i in range(nrules)), 'size': n} for m, n in groups[k]]}
            for k in sorted(groups, reverse=True)]
        return {'summary': {'baseCount': base, 'finalCount': final, 'lostCount': lost,
                            'percentMatched': pct(final, base)},
                'inclusionRuleStats': stats, 'treemapData': json.dumps({'name': 'Everyone', 'children': children})}

    @app.get('/WebAPI/cohortdefinition/{cid}/version/')
    @app.get('/WebAPI/cohortdefinition/{cid}/version')
    @app.get('/WebAPI/conceptset/{cid}/version/')
    @app.get('/WebAPI/conceptset/{cid}/version')
    def versions(cid: int):
        return []                       # 버전 이력은 아직 없다

    @app.get('/WebAPI/cohortsample/has-samples/{cid}')
    def has_samples(cid: int):
        return []

    @app.get('/WebAPI/cohortsample/{cid}/{key}')
    def samples(cid: int, key):
        return {'generationStatus': 'NONE', 'cohortSamples': []}

    @app.get('/WebAPI/trexsql/{key}/cache/status')
    def trexsql_status(key):
        return {'sourceKey': key, 'status': 'not_built', 'totalPatientCount': None, 'lastBuiltAt': None,
                'sizeBytes': None, 'errorMessage': None}

    # ---- 작업 ---------------------------------------------------------------------
    @app.get('/WebAPI/job/execution')
    def job_executions():
        jobs = store.jobs()
        return {'content': jobs, 'totalElements': len(jobs), 'totalPages': 1, 'size': len(jobs), 'number': 0,
                'first': True, 'last': True, 'numberOfElements': len(jobs), 'empty': not jobs,
                'pageable': {'pageNumber': 0, 'pageSize': max(len(jobs), 1)}}

    @app.get('/WebAPI/job/{jid}')
    def job_one(jid: int):
        j = store.job(jid)
        if j is None:
            raise HTTPException(404, 'job not found')
        return j

    # ---- 화면 ---------------------------------------------------------------------
    @app.get('/config-local.json')
    def config_local():
        return {'api': {'url': './WebAPI'}, 'userAuthenticationEnabled': False, 'enableSkipLogin': True,
                'enablePermissionManagement': False, 'enablePersonCount': False, 'enableTaggingSection': False,
                'authProviders': []}

    @app.get('/')
    def index():
        return FileResponse(os.path.join(STATIC, 'index.html'))

    app.mount('/', StaticFiles(directory=STATIC, html=True), name='atlas')
    return app
