"""Run Harness.java in parallel over several chunks. If a case hangs longer than TIMEOUT seconds, kill the process, record 'T' and restart from the next case."""
import os, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

CASES, OUT, NPROC = sys.argv[1], sys.argv[2], int(sys.argv[3])
TIMEOUT = float(sys.argv[4]) if len(sys.argv) > 4 else 30
HERE = os.environ.get('HARNESS_DIR') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'java')
CSV = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src', 'duckcdm', 'sqlrender',
                                   'csv', 'replacementPatterns.csv'))
# other harnesses such as Circe: HARNESS_DIR, HARNESS_MAIN, HARNESS_CP (extra classpath), no arguments
MAIN = os.environ.get('HARNESS_MAIN', 'Harness.java')
CP = '.' + (':' + os.environ['HARNESS_CP'] if os.environ.get('HARNESS_CP') else '')
ARGS = [] if os.environ.get('HARNESS_MAIN') else [CSV]
lines = open(CASES).read().splitlines(keepends=True)


def run_chunk(chunk):
    res = []
    i = 0
    while i < len(chunk):
        p = subprocess.Popen(['java', '-Xss64m', '-cp', CP, MAIN] + ARGS, cwd=HERE, stderr=subprocess.DEVNULL, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, bufsize=1)
        def feed(rest=chunk[i:]):
            try:
                p.stdin.writelines(rest)
                p.stdin.close()
            except BrokenPipeError:     # when we killed a hung process
                pass
        threading.Thread(target=feed, daemon=True).start()
        got = []
        timer = [None]
        def arm():
            if timer[0]:
                timer[0].cancel()
            timer[0] = threading.Timer(TIMEOUT if got else TIMEOUT + 20, p.kill)
            timer[0].start()
        arm()
        for out in p.stdout:
            got.append(out.rstrip('\n'))
            arm()
        timer[0].cancel()
        p.wait()
        res.extend(got)
        i += len(got)
        if i < len(chunk):          # the hung case
            res.append('T')
            i += 1
    return res


n = len(lines)
size = (n + NPROC - 1) // NPROC
chunks = [lines[k:k + size] for k in range(0, n, size)]
t0 = time.time()
with ThreadPoolExecutor(NPROC) as ex:
    outs = list(ex.map(run_chunk, chunks))
open(OUT, 'w').write('\n'.join(x for o in outs for x in o) + '\n')
print('java done', n, f'{time.time() - t0:.0f}s', sum(o.count('T') for o in outs), 'timeouts')
