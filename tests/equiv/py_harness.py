"""Same I/O as Harness.java, using the Python port."""
import base64, sys
from concurrent.futures import ProcessPoolExecutor

sys.setrecursionlimit(10000)


class _Timeout(BaseException):
    pass


def _alarm(*_):
    raise _Timeout()


def run(line, timeout=30):
    import signal
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(timeout)
    try:
        return _run(line)
    except _Timeout:
        return 'T'
    finally:
        signal.alarm(0)


def _run(line):
    from duckcdm.sqlrender import _render, _strings, _translate
    from duckcdm.sqlrender._java import JavaError
    d = lambda s: base64.b64decode(s).decode()
    op, dialect, sql, p = line.rstrip('\n').split('\t')
    try:
        sql = d(sql)
        if op == 'R':
            ks, vs = [], []
            if p:
                for kv in p.split(','):
                    k, v = kv.split(':')
                    ks.append(d(k)); vs.append(d(v))
            r = _render.render_sql(sql, ks, vs)
        elif op == 'T':
            r = _translate.translate_sql(sql, dialect, 'abcd1234', d(p) if p else None)
        elif op == '1':
            r = _translate.translate_single_statement_sql(sql, dialect, 'abcd1234', d(p) if p else None)
        else:
            r = '\0'.join(_strings.split_sql(sql))
        return 'O' + base64.b64encode(r.encode()).decode()
    except JavaError as e:
        return 'E' + base64.b64encode(f'JavaError: {e}'.encode()).decode()
    except Exception as e:
        return 'X' + base64.b64encode(f'{type(e).__name__}: {e}'.encode()).decode()


if __name__ == '__main__':
    lines = open(sys.argv[1]).readlines()
    with ProcessPoolExecutor(int(sys.argv[3]) if len(sys.argv) > 3 else 16) as ex:
        res = list(ex.map(run, lines, chunksize=8))
    open(sys.argv[2], 'w').write('\n'.join(res) + '\n')
