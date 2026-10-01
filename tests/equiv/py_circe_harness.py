"""Same I/O as CirceHarness.java, using the Python port."""
import base64, sys
from concurrent.futures import ProcessPoolExecutor

sys.setrecursionlimit(10000)


class _Timeout(BaseException):
    pass


def _alarm(*_):
    raise _Timeout()


def run(line, timeout=60):
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
    from duckcdm.circe import CirceError, build_cohort_query, build_concept_set_query
    d = lambda s: base64.b64decode(s).decode()
    op, js, opts = line.rstrip('\n').split('\t')
    try:
        if op == 'C':
            r = build_cohort_query(d(js), d(opts) if opts else None)
        else:
            r = build_concept_set_query(d(js))
        return 'O' + base64.b64encode(r.encode()).decode()
    except CirceError as e:
        return 'E' + base64.b64encode(f'CirceError: {e}'.encode()).decode()
    except RecursionError as e:
        return 'E' + base64.b64encode(f'StackOverflow: {e}'.encode()).decode()
    except Exception as e:
        return 'X' + base64.b64encode(f'{type(e).__name__}: {e}'.encode()).decode()


if __name__ == '__main__':
    lines = open(sys.argv[1]).readlines()
    with ProcessPoolExecutor(int(sys.argv[3]) if len(sys.argv) > 3 else 16) as ex:
        res = list(ex.map(run, lines, chunksize=4))
    open(sys.argv[2], 'w').write('\n'.join(res) + '\n')
