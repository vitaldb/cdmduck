"""Harness.java 를 여러 조각으로 병렬 실행. 한 케이스가 TIMEOUT 초 넘게 멈추면 프로세스를 죽이고 'T' 로 기록한 뒤 다음부터 재시작."""
import os, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

CASES, OUT, NPROC = sys.argv[1], sys.argv[2], int(sys.argv[3])
TIMEOUT = float(sys.argv[4]) if len(sys.argv) > 4 else 30
HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'java')
CSV = os.path.abspath(os.path.join(HERE, '..', '..', 'src', 'cdmduck', 'sqlrender', 'csv', 'replacementPatterns.csv'))
lines = open(CASES).read().splitlines(keepends=True)


def run_chunk(chunk):
    res = []
    i = 0
    while i < len(chunk):
        p = subprocess.Popen(['java', '-Xss64m', '-cp', '.', 'Harness.java', CSV], cwd=HERE, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, bufsize=1)
        def feed(rest=chunk[i:]):
            try:
                p.stdin.writelines(rest)
                p.stdin.close()
            except BrokenPipeError:     # 멈춘 프로세스를 죽였을 때
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
        if i < len(chunk):          # 멈춘 케이스
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
