"""The `duckcdm` command."""
import argparse
import os
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog='duckcdm', description='OMOP CDM / ATLAS-compatible tools on DuckDB')
    sub = ap.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('translate', help='Translate SQL Server dialect SQL into a target dialect')
    t.add_argument('file', help="SQL file ('-' for stdin)")
    t.add_argument('dialect')
    t.add_argument('--temp-schema')
    t.add_argument('-p', '--param', action='append', default=[], metavar='KEY=VALUE', help='Parameter to render before translating')
    sub.add_parser('dialects', help='List supported dialects')
    sv = sub.add_parser('serve', help='ATLAS 3.0 UI + WebAPI-compatible server (DuckDB)')
    sv.add_argument('db', nargs='+', help='CDM DuckDB file(s) (repeatable; use KEY=path to set the source key)')
    sv.add_argument('--memory-limit', default=None, help='DuckDB memory_limit (e.g. 8GB)')
    sv.add_argument('--threads', type=int, default=None, help='DuckDB threads')
    sv.add_argument('--cdm', default='main', help='CDM schema (default main)')
    sv.add_argument('--vocab', help='Vocabulary schema (default: CDM schema)')
    sv.add_argument('--results', default='results', help='Results schema (default results, created if missing)')
    sv.add_argument('--store', help='Store file for concept sets and cohort definitions (default: duckcdm_store.duckdb next to the first DB)')
    sv.add_argument('--host', default='127.0.0.1')
    sv.add_argument('--port', type=int, default=8080)
    sv.add_argument('--user', default='admin')
    sv.add_argument('--gateway', action='store_true',
                    help='only accept requests relayed by a trusted authenticating gateway (see --gateway-* options)')
    sv.add_argument('--gateway-ip', default='172.23.', help='comma-separated trusted peer address prefixes')
    sv.add_argument('--gateway-marker', default='X-Auth-Method=parent_gateway',
                    help='HEADER=VALUE the gateway always sets ("" to disable)')
    sv.add_argument('--gateway-log', help='append one JSON audit line per request (peer, verdict, identity headers)')
    sv.add_argument('--allow-users', help='file with one allowed user id per line (re-read when it changes)')
    sv.add_argument('--achilles', action='store_true', help='Compute Achilles results at startup if missing (for data source reports)')
    vw = sub.add_parser('views', help='Parquet directory (one subfolder per table) -> DuckDB file of views with only the OMOP standard columns')
    vw.add_argument('parquet_dir')
    vw.add_argument('db', help='DuckDB file to create (holds only views and the results schema)')
    vw.add_argument('--schema', default='main')
    ac = sub.add_parser('achilles', help='Compute Achilles analyses for data source reports in DuckDB')
    ac.add_argument('db')
    ac.add_argument('--memory-limit', default=None, help='DuckDB memory_limit (e.g. 8GB)')
    ac.add_argument('--threads', type=int, default=None, help='DuckDB threads')
    ac.add_argument('--cdm', default='main')
    ac.add_argument('--vocab')
    ac.add_argument('--results', default='results')
    ac.add_argument('--small-cell', type=int, default=5, help='Remove cells with counts at or below this (Achilles default 5)')
    c = sub.add_parser('cohort', help='Cohort definition (ATLAS/Circe JSON) -> target dialect SQL')
    c.add_argument('file', help="Cohort JSON ('-' for stdin)")
    c.add_argument('dialect', nargs='?', default='duckdb')
    c.add_argument('--cdm', default='main', help='CDM schema')
    c.add_argument('--vocab', help='Vocabulary schema (default: CDM schema)')
    c.add_argument('--results', default='main', help='Results schema')
    c.add_argument('--target', default=None, help='Target cohort table (default: <results schema>.cohort)')
    c.add_argument('--cohort-id', type=int, default=1)
    c.add_argument('--stats', action='store_true', help='Also generate inclusion rule statistics')
    c.add_argument('--ohdsi', action='store_true', help='Emit untranslated OHDSI SQL as is (SQL Server dialect, with parameters)')
    a = ap.parse_args(argv)
    from .sqlrender import dialects, render, translate
    if a.cmd == 'serve':
        import uvicorn
        from .webapi.app import create_app
        from .webapi.sources import Source
        from .webapi.store import Store
        sources = []
        for i, spec in enumerate(a.db, 1):
            key, _, path = spec.rpartition('=') if '=' in spec else ('', '', spec)
            key = key or os.path.splitext(os.path.basename(path))[0].upper()
            sources.append(Source(i, key, key, path, a.cdm, a.vocab, a.results, memory_limit=a.memory_limit,
                                  threads=a.threads))
        if a.achilles:
            from .webapi.reports import Reports
            for s in sources:
                r = Reports(s)
                if not r.ready():
                    print(f'{s.key}: computing Achilles analyses…')
                    done, failed = r.build()
                    print(f'{s.key}: {len(done)} analyses done, {len(failed)} failed')
        store_path = a.store or os.path.join(os.path.dirname(os.path.abspath(sources[0].path)), 'duckcdm_store.duckdb')
        gw = None
        if a.gateway:
            from .webapi.gateway import Gateway
            name, _, value = a.gateway_marker.partition('=')
            gw = Gateway(a.gateway_ip.split(','), name or None, value, a.allow_users, a.gateway_log)
        app = create_app(sources, Store(store_path, a.user), gateway=gw)
        print(f'duckcdm {__import__("duckcdm").__version__}: http://{a.host}:{a.port}/  (API: /WebAPI, store: {store_path})')
        uvicorn.run(app, host=a.host, port=a.port, log_level='warning')
        return 0
    if a.cmd == 'views':
        import duckdb
        from .omop import create_parquet_views
        con = duckdb.connect(a.db)
        create_parquet_views(con, os.path.abspath(a.parquet_dir), a.schema)
        con.close()
        return 0
    if a.cmd == 'achilles':
        import time
        import duckdb
        from .achilles import run_achilles
        from .webapi.reports import Reports
        from .webapi.sources import Source
        t0 = time.time()
        src = Source(1, 'CDM', 'CDM', a.db, a.cdm, a.vocab, a.results, memory_limit=a.memory_limit, threads=a.threads)
        done, failed = Reports(src).build(log=print)
        print(f'{len(done)} analyses, {len(failed)} failed, {time.time() - t0:.1f}s')
        for aid, err in failed:
            print('  FAILED', aid, err)
        return 1 if failed else 0
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
