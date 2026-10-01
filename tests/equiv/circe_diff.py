"""Compare Circe java.out / py.out."""
import base64, collections, json, sys

d = lambda s: base64.b64decode(s).decode()
cases = open(sys.argv[1]).read().splitlines()
j = open(sys.argv[2]).read().splitlines()
p = open(sys.argv[3]).read().splitlines()
st = collections.Counter()
bad = collections.defaultdict(list)
jerr = collections.Counter()
for i, (c, a, b) in enumerate(zip(cases, j, p)):
    if a[0] == 'E':
        jerr[d(a[1:]).split(':')[0]] += 1
    if a == b:
        k = 'same'
    elif a[0] == 'E' and b[0] == 'E':
        k = 'both_err'
    else:
        k = 'DIFF:' + a[0] + b[0]
    st[k] += 1
    if k.startswith('DIFF'):
        bad[k].append(i)
print(dict(st))
print('java errors:', jerr.most_common(8))
if len(sys.argv) > 4:
    json.dump({k: v[:200] for k, v in bad.items()}, open(sys.argv[4], 'w'))
