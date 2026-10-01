"""java.out 과 py.out 비교. 예외는 둘 다 예외면 일치로 본다(메시지도 비교해 따로 센다)."""
import base64, collections, sys

cases = open(sys.argv[1]).read().splitlines()
j = open(sys.argv[2]).read().splitlines()
p = open(sys.argv[3]).read().splitlines()
assert len(cases) == len(j) == len(p), (len(cases), len(j), len(p))
d = lambda s: base64.b64decode(s).decode()
stat = collections.Counter()
bad = collections.defaultdict(list)
for c, a, b in zip(cases, j, p):
    op, dialect = c.split('\t')[:2]
    key = op if op != 'T' else 'T:' + dialect
    stat[key, 'n'] += 1
    if a == b:
        stat[key, 'same'] += 1
    elif a[0] == 'E' and b[0] == 'E':
        stat[key, 'both_err'] += 1
        bad['msg'].append((c, d(a[1:]), d(b[1:])))
    else:
        stat[key, 'DIFF'] += 1
        bad[key].append((c, d(a[1:]) if a[0] != 'X' else a, d(b[1:])))
keys = sorted({k for k, _ in stat})
tot = collections.Counter()
for k in keys:
    row = {m: stat[k, m] for m in ('n', 'same', 'both_err', 'DIFF')}
    tot.update(row)
    print(f'{k:22s} n={row["n"]:6d} same={row["same"]:6d} both_err={row["both_err"]:4d} DIFF={row["DIFF"]:4d}')
print('TOTAL', dict(tot))
if len(sys.argv) > 4:
    import json
    json.dump({k: [(c, a, b) for c, a, b in v[:int(sys.argv[5]) if len(sys.argv) > 5 else 5]] for k, v in bad.items()},
              open(sys.argv[4], 'w'), ensure_ascii=False, indent=1)
