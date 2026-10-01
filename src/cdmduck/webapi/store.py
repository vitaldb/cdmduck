"""앱 저장소(WebAPI 의 'webapi' 스키마에 해당): 개념집합·코호트 정의·생성 이력. DuckDB 파일 하나."""
import json
import threading
import time

import duckdb

DDL = """
CREATE SEQUENCE IF NOT EXISTS seq_concept_set START 1;
CREATE SEQUENCE IF NOT EXISTS seq_cohort START 1;
CREATE SEQUENCE IF NOT EXISTS seq_job START 1;
CREATE TABLE IF NOT EXISTS concept_set (
  id INTEGER PRIMARY KEY, name VARCHAR, description VARCHAR,
  created_by VARCHAR, created_date BIGINT, modified_by VARCHAR, modified_date BIGINT);
CREATE TABLE IF NOT EXISTS concept_set_item (
  concept_set_id INTEGER, concept_id BIGINT, is_excluded INTEGER, include_descendants INTEGER, include_mapped INTEGER,
  ord INTEGER);
CREATE TABLE IF NOT EXISTS cohort_definition (
  id INTEGER PRIMARY KEY, name VARCHAR, description VARCHAR, expression_type VARCHAR, expression VARCHAR,
  created_by VARCHAR, created_date BIGINT, modified_by VARCHAR, modified_date BIGINT);
CREATE TABLE IF NOT EXISTS cohort_generation_info (
  cohort_definition_id INTEGER, source_id INTEGER, start_time BIGINT, execution_duration INTEGER, status VARCHAR,
  is_valid BOOLEAN, is_canceled BOOLEAN, fail_message VARCHAR, person_count BIGINT, record_count BIGINT,
  PRIMARY KEY (cohort_definition_id, source_id));
CREATE TABLE IF NOT EXISTS job_execution (
  execution_id INTEGER PRIMARY KEY, job_name VARCHAR, status VARCHAR, start_date BIGINT, end_date BIGINT,
  exit_status VARCHAR, parameters VARCHAR);
"""


def now_ms():
    return int(time.time() * 1000)


class Store:
    def __init__(self, path, user='admin'):
        self.con = duckdb.connect(path)
        self.lock = threading.RLock()
        self.user = {'id': 1, 'login': user, 'name': user}
        for stmt in DDL.strip().split(';'):
            if stmt.strip():
                self.con.execute(stmt)

    def _rows(self, sql, params=()):
        with self.lock:
            cur = self.con.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _exec(self, sql, params=()):
        with self.lock:
            self.con.execute(sql, params)

    _SEQ_TABLE = {'seq_concept_set': ('concept_set', 'id'), 'seq_cohort': ('cohort_definition', 'id'),
                  'seq_job': ('job_execution', 'execution_id')}

    def _next(self, seq):
        # 시퀀스 대신 max+1: 프로세스가 비정상 종료돼도 번호가 되돌아가지 않는다
        table, col = self._SEQ_TABLE[seq]
        with self.lock:
            return self.con.execute(f'SELECT coalesce(max({col}), 0) + 1 FROM {table}').fetchone()[0]

    def _common(self, r):
        # 화면의 zod 스키마는 optional 이지 nullable 이 아니다 — 없는 값은 키를 빼야 한다
        d = {'createdBy': {'id': 1, 'login': r['created_by'], 'name': r['created_by']},
             'createdDate': r['created_date'], 'tags': [], 'hasWriteAccess': True, 'hasReadAccess': True,
             'writeAccess': True, 'readAccess': True}
        if r['modified_by']:
            d['modifiedBy'] = {'id': 1, 'login': r['modified_by'], 'name': r['modified_by']}
            d['modifiedDate'] = r['modified_date']
        return d

    # ---- 개념집합
    def list_concept_sets(self):
        return [dict(id=r['id'], name=r['name'], description=r['description'], **self._common(r))
                for r in self._rows('SELECT * FROM concept_set ORDER BY id')]

    def get_concept_set(self, cid):
        rows = self._rows('SELECT * FROM concept_set WHERE id = ?', [cid])
        if not rows:
            return None
        r = rows[0]
        return dict(id=r['id'], name=r['name'], description=r['description'], **self._common(r))

    def create_concept_set(self, name, description):
        cid = self._next('seq_concept_set')
        t = now_ms()
        self._exec('INSERT INTO concept_set VALUES (?,?,?,?,?,?,?)',
                   [cid, name, description, self.user['login'], t, None, None])
        return self.get_concept_set(cid)

    def update_concept_set(self, cid, name, description):
        self._exec('UPDATE concept_set SET name = ?, description = ?, modified_by = ?, modified_date = ? WHERE id = ?',
                   [name, description, self.user['login'], now_ms(), cid])
        return self.get_concept_set(cid)

    def delete_concept_set(self, cid):
        self._exec('DELETE FROM concept_set_item WHERE concept_set_id = ?', [cid])
        self._exec('DELETE FROM concept_set WHERE id = ?', [cid])

    def get_items(self, cid):
        return [{'id': i, 'conceptSetId': cid, 'conceptId': r['concept_id'], 'isExcluded': r['is_excluded'],
                 'includeDescendants': r['include_descendants'], 'includeMapped': r['include_mapped']}
                for i, r in enumerate(self._rows(
                    'SELECT * FROM concept_set_item WHERE concept_set_id = ? ORDER BY ord', [cid]), 1)]

    def set_items(self, cid, items):
        with self.lock:
            self._exec('DELETE FROM concept_set_item WHERE concept_set_id = ?', [cid])
            for i, it in enumerate(items):
                self._exec('INSERT INTO concept_set_item VALUES (?,?,?,?,?,?)',
                           [cid, int(it['conceptId']), int(bool(it.get('isExcluded'))),
                            int(bool(it.get('includeDescendants'))), int(bool(it.get('includeMapped'))), i])
            self._exec('UPDATE concept_set SET modified_by = ?, modified_date = ? WHERE id = ?',
                       [self.user['login'], now_ms(), cid])

    # ---- 코호트 정의
    def list_cohorts(self):
        return [dict(id=r['id'], name=r['name'], description=r['description'], expressionType=r['expression_type'],
                     **self._common(r))
                for r in self._rows('SELECT * FROM cohort_definition ORDER BY id')]

    def get_cohort(self, cid):
        rows = self._rows('SELECT * FROM cohort_definition WHERE id = ?', [cid])
        if not rows:
            return None
        r = rows[0]
        return dict(id=r['id'], name=r['name'], description=r['description'], expressionType=r['expression_type'],
                    expression=r['expression'], **self._common(r))

    def create_cohort(self, name, description, expression_type, expression_json):
        cid = self._next('seq_cohort')
        self._exec('INSERT INTO cohort_definition VALUES (?,?,?,?,?,?,?,?,?)',
                   [cid, name, description, expression_type or 'SIMPLE_EXPRESSION', expression_json,
                    self.user['login'], now_ms(), None, None])
        return self.get_cohort(cid)

    def update_cohort(self, cid, name, description, expression_type, expression_json):
        self._exec('UPDATE cohort_definition SET name=?, description=?, expression_type=?, expression=?, '
                   'modified_by=?, modified_date=? WHERE id=?',
                   [name, description, expression_type or 'SIMPLE_EXPRESSION', expression_json, self.user['login'],
                    now_ms(), cid])
        return self.get_cohort(cid)

    def delete_cohort(self, cid):
        self._exec('DELETE FROM cohort_generation_info WHERE cohort_definition_id = ?', [cid])
        self._exec('DELETE FROM cohort_definition WHERE id = ?', [cid])

    # ---- 생성 이력
    def set_generation(self, cid, source_id, **fields):
        with self.lock:
            rows = self._rows('SELECT * FROM cohort_generation_info WHERE cohort_definition_id=? AND source_id=?',
                              [cid, source_id])
            cur = rows[0] if rows else {'cohort_definition_id': cid, 'source_id': source_id, 'start_time': now_ms(),
                                        'execution_duration': None, 'status': 'PENDING', 'is_valid': False,
                                        'is_canceled': False, 'fail_message': None, 'person_count': None,
                                        'record_count': None}
            cur.update(fields)
            self._exec('DELETE FROM cohort_generation_info WHERE cohort_definition_id=? AND source_id=?',
                       [cid, source_id])
            self._exec('INSERT INTO cohort_generation_info VALUES (?,?,?,?,?,?,?,?,?,?)',
                       [cid, source_id, cur['start_time'], cur['execution_duration'], cur['status'], cur['is_valid'],
                        cur['is_canceled'], cur['fail_message'], cur['person_count'], cur['record_count']])

    def generation_info(self, cid):
        return [{'id': {'cohortDefinitionId': r['cohort_definition_id'], 'sourceId': r['source_id']},
                 'startTime': r['start_time'], 'executionDuration': r['execution_duration'], 'status': r['status'],
                 'isValid': r['is_valid'], 'isCanceled': r['is_canceled'], 'failMessage': r['fail_message'],
                 'personCount': r['person_count'], 'recordCount': r['record_count'], 'createdBy': self.user}
                for r in self._rows('SELECT * FROM cohort_generation_info WHERE cohort_definition_id=?', [cid])]

    # ---- 작업
    def start_job(self, job_name, parameters):
        jid = self._next('seq_job')
        self._exec('INSERT INTO job_execution VALUES (?,?,?,?,?,?,?)',
                   [jid, job_name, 'STARTED', now_ms(), None, None, json.dumps(parameters)])
        return jid

    def finish_job(self, jid, status, exit_status=None):
        self._exec('UPDATE job_execution SET status=?, end_date=?, exit_status=? WHERE execution_id=?',
                   [status, now_ms(), exit_status, jid])

    def job(self, jid):
        rows = self._rows('SELECT * FROM job_execution WHERE execution_id=?', [jid])
        return self._job_dict(rows[0]) if rows else None

    def jobs(self):
        return [self._job_dict(r) for r in self._rows('SELECT * FROM job_execution ORDER BY execution_id DESC')]

    def _job_dict(self, r):
        return {'executionId': r['execution_id'], 'status': r['status'], 'startDate': r['start_date'],
                'endDate': r['end_date'], 'exitStatus': r['exit_status'],
                'jobInstance': {'instanceId': r['execution_id'], 'name': r['job_name']},
                'jobParameters': json.loads(r['parameters'] or '{}'), 'ownerType': 'user'}
