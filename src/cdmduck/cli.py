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
