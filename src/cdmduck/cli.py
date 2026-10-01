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
    a = ap.parse_args(argv)
    from .sqlrender import dialects, render, translate
    if a.cmd == 'dialects':
        print('\n'.join(dialects()))
    elif a.cmd == 'translate':
        sql = sys.stdin.read() if a.file == '-' else open(a.file, encoding='utf-8').read()
        if a.param:
            sql = render(sql, **dict(p.split('=', 1) for p in a.param))
        sys.stdout.write(translate(sql, a.dialect, temp_emulation_schema=a.temp_schema))
    return 0


if __name__ == '__main__':
    sys.exit(main())
