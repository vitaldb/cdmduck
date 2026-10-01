"""명령어 `cdmduck`."""
import argparse
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog='cdmduck', description='OMOP CDM / ATLAS-compatible tools on DuckDB')
    sub = ap.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('translate', help='SQL Server 방언 SQL 을 대상 방언으로 번역')
    t.add_argument('file', help="SQL 파일 ('-' 는 표준입력)")
    t.add_argument('dialect')
    t.add_argument('--temp-schema')
    t.add_argument('-p', '--param', action='append', default=[], metavar='KEY=VALUE', help='번역 전에 렌더할 파라미터')
    sub.add_parser('dialects', help='지원 방언 목록')
    sv = sub.add_parser('serve', help='ATLAS 3.0 화면 + WebAPI 호환 서버 (DuckDB)')
    sv.add_argument('db', nargs='+', help='CDM DuckDB 파일 (여러 개 가능; KEY=경로 로 소스 키 지정)')
    sv.add_argument('--cdm', default='main', help='CDM 스키마 (기본 main)')
    sv.add_argument('--vocab', help='어휘 스키마 (기본: CDM 스키마)')
    sv.add_argument('--results', default='results', help='결과 스키마 (기본 results, 없으면 만든다)')
    sv.add_argument('--store', help='개념집합·코호트 정의 저장 파일 (기본: 첫 DB 옆 cdmduck_store.duckdb)')
    sv.add_argument('--host', default='127.0.0.1')
    sv.add_argument('--port', type=int, default=8080)
    sv.add_argument('--user', default='admin')
    c = sub.add_parser('cohort', help='코호트 정의(ATLAS/Circe JSON) → 대상 방언 SQL')
    c.add_argument('file', help="코호트 JSON ('-' 는 표준입력)")
    c.add_argument('dialect', nargs='?', default='duckdb')
    c.add_argument('--cdm', default='main', help='CDM 스키마')
    c.add_argument('--vocab', help='어휘 스키마(기본: CDM 스키마)')
    c.add_argument('--results', default='main', help='결과 스키마')
    c.add_argument('--target', default=None, help='대상 코호트 테이블(기본: <결과스키마>.cohort)')
    c.add_argument('--cohort-id', type=int, default=1)
    c.add_argument('--stats', action='store_true', help='포함규칙 통계도 생성')
    c.add_argument('--ohdsi', action='store_true', help='번역하지 않은 OHDSI SQL(SQL Server 방언, 파라미터 포함) 그대로')
    a = ap.parse_args(argv)
    from .sqlrender import dialects, render, translate
    if a.cmd == 'serve':
        import os
        import uvicorn
        from .webapi.app import create_app
        from .webapi.sources import Source
        from .webapi.store import Store
        sources = []
        for i, spec in enumerate(a.db, 1):
            key, _, path = spec.rpartition('=') if '=' in spec else ('', '', spec)
            key = key or os.path.splitext(os.path.basename(path))[0].upper()
            sources.append(Source(i, key, key, path, a.cdm, a.vocab, a.results))
        store_path = a.store or os.path.join(os.path.dirname(os.path.abspath(sources[0].path)), 'cdmduck_store.duckdb')
        app = create_app(sources, Store(store_path, a.user))
        print(f'cdmduck {__import__("cdmduck").__version__}: http://{a.host}:{a.port}/  (API: /WebAPI, store: {store_path})')
        uvicorn.run(app, host=a.host, port=a.port, log_level='warning')
        return 0
    if a.cmd == 'cohort':
        from .circe import build_cohort_query
        text = sys.stdin.read() if a.file == '-' else open(a.file, encoding='utf-8').read()
        sql = build_cohort_query(text, cdm_schema=a.cdm, vocabulary_schema=a.vocab or a.cdm,
                                 result_schema=a.results, target_table=a.target or a.results + '.cohort',
                                 cohort_id=a.cohort_id, generate_stats=a.stats)
        sys.stdout.write(sql if a.ohdsi else translate(render(sql), a.dialect))
    elif a.cmd == 'dialects':
        print('\n'.join(dialects()))
    elif a.cmd == 'translate':
        sql = sys.stdin.read() if a.file == '-' else open(a.file, encoding='utf-8').read()
        if a.param:
            sql = render(sql, **dict(p.split('=', 1) for p in a.param))
        sys.stdout.write(translate(sql, a.dialect, temp_emulation_schema=a.temp_schema))
    return 0


if __name__ == '__main__':
    sys.exit(main())
