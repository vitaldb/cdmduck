"""Cases for Java/Python comparison: strings from SqlRender R tests, SQL files from Circe and WebAPI, synthetic render cases."""
import base64, glob, os, random, re, sys

SRC = sys.argv[1]          # directory holding the clones (sqlrender, circe-be, webapi3)
OUT = sys.argv[2]
DIALECTS = ['sql server', 'oracle', 'postgresql', 'redshift', 'pdw', 'synapse', 'impala', 'netezza', 'bigquery',
            'sqlite', 'sqlite extended', 'hive', 'spark', 'snowflake', 'iris', 'duckdb']
b = lambda s: base64.b64encode(s.encode()).decode()
rng = random.Random(20260930)
cases = []


def r_unescape(s):
    return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t', 'r': '\r'}.get(m.group(1), m.group(1)), s)


# 1) string literals from the R tests
lits = []
for f in sorted(glob.glob(f'{SRC}/sqlrender/tests/testthat/*.R')):
    txt = open(f, encoding='utf-8').read()
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', txt, re.S):
        s = r_unescape(m.group(1))
        if len(s) >= 6 and re.search(r'[A-Za-z]', s):
            lits.append(s)
lits = list(dict.fromkeys(lits))
for s in lits:
    for d in DIALECTS:
        cases.append(('T', d, s, ''))
    cases.append(('T', 'oracle', s, b('scratch')))
    cases.append(('R', '', s, ''))
    cases.append(('S', '', s, ''))
    cases.append(('1', 'duckdb', s, ''))

# 2) real SQL files
files = sorted(glob.glob(f'{SRC}/circe-be/**/*.sql', recursive=True) + glob.glob(f'{SRC}/webapi3/**/*.sql', recursive=True))
bodies = []
for f in files:
    try:
        s = open(f, encoding='utf-8').read()
    except UnicodeDecodeError:
        continue
    bodies.append(s)
    cases.append(('S', '', s, ''))
    cases.append(('R', '', s, ''))
    # render with randomly filled parameters
    names = list(dict.fromkeys(re.findall(r'@([A-Za-z_][A-Za-z0-9_]*)', s)))
    for k in range(2):
        vals = [rng.choice(['TRUE', 'FALSE', '1', '0', 'cdm', 'results', 'main.cdm', "'x'", '12,34', 'a$b\\c'])
                for _ in names]
        cases.append(('R', '', s, ','.join(f'{b(n)}:{b(v)}' for n, v in zip(names, vals))))
    for d in DIALECTS:
        cases.append(('T', d, s, ''))

# 3) synthetic conditional-expression renders
atoms = ['@a', '@b', 'TRUE', 'FALSE', '1', '0', "'x'", '@c']
ops = ['==', '!=', '<>', ' & ', ' | ']
for i in range(3000):
    def expr(depth=0):
        if depth < 2 and rng.random() < .3:
            return '(' + expr(depth + 1) + ')'
        if rng.random() < .15:
            return f"@a IN ({','.join(rng.sample(['1','2','x',chr(39)+'x'+chr(39)], 2))})"
        if rng.random() < .4:
            return rng.choice(atoms)
        return rng.choice(atoms) + rng.choice(ops) + rng.choice(atoms)
    body = f'SELECT 1 {{{expr()}}} ? {{AND x = @b}} : {{AND y = @c {{@a}}?{{/*n*/ z}}}}'
    if rng.random() < .3:
        body = '{DEFAULT @c = ' + rng.choice(['1', "'q'", '@a']) + '}\n' + body
    vals = {'a': rng.choice(['1', '0', 'x', 'TRUE']), 'b': rng.choice(['1', '0', "'x'", 'b']), 'c': rng.choice(['1', 'x'])}
    ks = rng.sample(list(vals), rng.randint(0, 3))
    cases.append(('R', '', body, ','.join(f'{b(k)}:{b(vals[k])}' for k in ks)))

with open(OUT, 'w') as f:
    for op, d, s, p in cases:
        f.write(f'{op}\t{d}\t{b(s)}\t{p}\n')
print(len(lits), 'literals', len(bodies), 'files', len(cases), 'cases')
