"""Cases for Circe equivalence testing: PhenotypeLibrary cohorts, their concept sets, circe test JSON, random cohorts.

python3 circe_corpus.py <phenotype cohorts dir> <circe-be dir> out.tsv [random count]
"""
import base64, glob, json, os, random, sys

PHL, CIRCE, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
N_FUZZ = int(sys.argv[4]) if len(sys.argv) > 4 else 6000
b = lambda s: base64.b64encode(s.encode()).decode()
rng = random.Random(20261001)
cases = []
OPTIONS = ['', json.dumps({'cdmSchema': 'cdm', 'resultSchema': 'res', 'targetTable': 'res.cohort', 'cohortId': 7,
                           'generateStats': True}),
           json.dumps({'vocabularySchema': 'vocab', 'cohortIdFieldName': 'cid', 'generateStats': False})]


def add_cohort(text, opts=('',)):
    for o in opts:
        cases.append(('C', text, o))


# 1) PhenotypeLibrary
for f in sorted(glob.glob(os.path.join(PHL, '*.json'))):
    text = open(f, encoding='utf-8').read()
    add_cohort(text, OPTIONS)
    try:
        for cs in json.loads(text).get('ConceptSets') or []:
            if isinstance(cs, dict) and cs.get('expression') is not None:
                cases.append(('S', json.dumps(cs['expression']), ''))
    except ValueError:
        pass

# 2) circe's own test JSON
for f in sorted(glob.glob(os.path.join(CIRCE, 'src', 'test', 'resources', '**', '*.json'), recursive=True)):
    try:
        add_cohort(open(f, encoding='utf-8').read(), OPTIONS[:2])
    except UnicodeDecodeError:
        pass

# 3) random cohorts
OPS_N = ['lt', 'lte', 'eq', '!eq', 'gt', 'gte', 'bt', '!bt']
OPS_T = ['startsWith', 'contains', 'endsWith', '!startsWith', '!contains', '!endsWith']
COLS = ['DAYS_SUPPLY', 'DOMAIN_CONCEPT', 'DOMAIN_SOURCE_CONCEPT', 'DURATION', 'END_DATE', 'ERA_OCCURRENCES', 'GAP_DAYS',
        'QUANTITY', 'RANGE_HIGH', 'RANGE_LOW', 'REFILLS', 'START_DATE', 'UNIT', 'VALUE_AS_NUMBER', 'VISIT_ID',
        'VISIT_DETAIL_ID']


def maybe(p=0.5):
    return rng.random() < p


CLEAN = os.environ.get('CIRCE_CLEAN') == '1'     # if 1, no deliberately wrong values -> broader coverage of successful SQL paths


def edge(p=0.03):
    return not CLEAN and rng.random() < p


def num():
    if edge(0.05):
        return rng.choice([None, '5', '2.5', 'abc', True, 1e10, -0.0, 2**40, 0.125, 2.675, 1.00005, -1.5])
    return rng.choice([rng.randint(-5, 120), round(rng.uniform(-10, 500), rng.randint(0, 6))])


def nrange():
    op = rng.choice(OPS_N) if not edge() else rng.choice(['xx', None, 'BT'])
    r = {'Value': num(), 'Op': op}
    if op and op.endswith('bt') or maybe(0.2):
        r['Extent'] = num()
    return r


def date():
    if edge(0.03):
        return rng.choice(['2010-1', 'abc', '2010--01-02', '2010-01-02-03', None, '+2010-01-01', '99999999999-1-1'])
    return '%04d-%02d-%02d' % (rng.randint(1990, 2025), rng.randint(1, 12), rng.randint(1, 28))


def drange():
    op = rng.choice(OPS_N) if not edge() else rng.choice(['xx', None])
    r = {'Value': date(), 'Op': op}
    if op and op.endswith('bt') or maybe(0.2):
        r['Extent'] = date()
    return r


def concepts():
    n = rng.randint(0, 3)
    out = []
    for _ in range(n):
        c = {'CONCEPT_ID': rng.choice([rng.randint(1, 50000000), None]) if edge(0.05) else rng.randint(1, 50000000),
             'CONCEPT_NAME': 'c'}
        out.append(c)
    return out


def cs_sel():
    return {'CodesetId': rng.choice([None, rng.randint(0, 9)]) if edge(0.2) else rng.randint(0, 9),
            'IsExclusion': maybe(0.3)}


def text():
    return {'Text': rng.choice(["abc", "a'b", "x\\'y", "%", "", None, "O''Neil"]),
            'Op': rng.choice(OPS_T) if not edge() else None}


def da():
    return {'StartWith': rng.choice(['START_DATE', 'END_DATE', 0, 1]), 'StartOffset': rng.randint(-30, 30),
            'EndWith': rng.choice(['START_DATE', 'END_DATE']), 'EndOffset': rng.randint(-30, 30)}


def period():
    return {k: date() for k in ('StartDate', 'EndDate') if maybe(0.7)}


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from duckcdm.circe import _model as M      # noqa: E402  (uses only the field-name lists — checked earlier to match Java)

CONV_GEN = {M._nr: nrange, M._concepts: concepts, M._cs: cs_sel, M._dr: drange, M._tf: text,
            M.Period.parse: period, M.DateAdjustment.parse: da,
            M.to_integer: lambda: rng.randint(0, 9), M.to_boolean: lambda: rng.choice([True, False, None]),
            M.to_bool: lambda: rng.choice([True, False])}
WRONG = [lambda: 'x', lambda: 3, lambda: 2.5, lambda: True, lambda: [], lambda: {}, lambda: None, lambda: '7']
FIELDS = {t: [(j, conv) for j, _, conv, _ in c._all_fields() if j not in ('CorrelatedCriteria', 'CriteriaList')]
          for t, c in M.CRITERIA_TYPES.items()}


def gen_by_conv(conv):
    if edge(0.01):
        return rng.choice(WRONG)()          # deliberately wrong type — exercises Jackson coercion and error paths
    return CONV_GEN[conv]()


def criteria(depth=0):
    t = rng.choice(list(FIELDS))
    if t == 'CustomEra' and depth > 1:
        t = 'ConditionOccurrence'
    body = {}
    for f, conv in FIELDS[t]:
        if maybe(0.25):
            body[f] = gen_by_conv(conv)
    if t == 'CustomEra':
        body['CriteriaList'] = [criteria(depth + 1) for _ in range(rng.randint(0 if edge(0.1) else 1, 2))]
        body['GapDays'] = rng.choice([None, rng.randint(0, 30)])
    if depth < 2 and maybe(0.25):
        body['CorrelatedCriteria'] = group(depth + 1)
    return {t: body}


def endpoint():
    e = {'Coeff': rng.choice([-1, 1]) if not edge(0.05) else rng.choice([0, 2, 70000])}
    if maybe(0.7):
        e['Days'] = rng.randint(0, 3650) if not edge(0.05) else 2**31 - 1
    return e


def window():
    w = {'Start': endpoint(), 'End': endpoint()}
    for k in ('UseIndexEnd', 'UseEventEnd'):
        if maybe(0.4):
            w[k] = rng.choice([True, False, None])
    return w


def corelated(depth):
    c = {'Criteria': criteria(depth), 'StartWindow': window(),
         'Occurrence': {'Type': rng.choice([0, 1, 2]) if not edge(0.03) else 3, 'Count': rng.randint(0, 3),
                        'IsDistinct': maybe(0.3)}}
    if c['Occurrence']['IsDistinct'] and maybe(0.5):
        c['Occurrence']['CountColumn'] = rng.choice(['DOMAIN_CONCEPT', 'START_DATE', 'END_DATE'] if CLEAN else COLS)
    if maybe(0.4):
        c['EndWindow'] = window()
    for k in ('RestrictVisit', 'IgnoreObservationPeriod'):
        if maybe(0.3):
            c[k] = maybe()
    return c


def demographic():
    d = {}
    for k, g in [('Age', nrange), ('Gender', concepts), ('GenderCS', cs_sel), ('Race', concepts), ('RaceCS', cs_sel),
                 ('Ethnicity', concepts), ('EthnicityCS', cs_sel), ('OccurrenceStartDate', drange),
                 ('OccurrenceEndDate', drange)]:
        if maybe(0.3):
            d[k] = g()
    return d


def group(depth=0):
    g = {'Type': rng.choice(['ALL', 'ANY', 'AT_LEAST', 'AT_MOST', 'all', 'at_least']) if not edge(0.05)
         else rng.choice(['XYZ', None]),
         'CriteriaList': [corelated(depth) for _ in range(rng.randint(0, 2))],
         'DemographicCriteriaList': [demographic() for _ in range(rng.randint(0, 1))],
         'Groups': [group(depth + 1) for _ in range(rng.randint(0, 1 if depth < 2 else 0))]}
    if g['Type'] and g['Type'].upper().startswith('AT_') or maybe(0.2):
        g['Count'] = rng.choice([0, 1, 2]) if not edge(0.05) else None
    return g


def concept_set(i):
    items = [{'concept': {'CONCEPT_ID': rng.randint(1, 5000000)}, 'isExcluded': maybe(0.2),
              'includeDescendants': maybe(0.5), 'includeMapped': maybe(0.2)} for _ in range(rng.randint(0, 4))]
    if edge(0.02):
        items.append({'concept': None})
    return {'id': i, 'name': 'cs%d' % i, 'expression': {'items': items}}


def cohort():
    c = {'ConceptSets': [concept_set(i) for i in range(rng.randint(0, 4))],
         'PrimaryCriteria': {'CriteriaList': [criteria() for _ in range(rng.randint(1, 3))],
                             'ObservationWindow': {'PriorDays': rng.randint(0, 365), 'PostDays': rng.randint(0, 30)},
                             'PrimaryCriteriaLimit': {'Type': rng.choice(['First', 'All', 'Last', 'ALL'] + ([] if CLEAN else [None]))}},
         'QualifiedLimit': {'Type': rng.choice(['First', 'All', 'Last', None])},
         'ExpressionLimit': {'Type': rng.choice(['First', 'All', 'Last', None])},
         'InclusionRules': [{'name': 'r%d' % i, 'expression': group()} for i in range(rng.randint(0, 3))],
         'CollapseSettings': {'CollapseType': 'ERA', 'EraPad': rng.randint(0, 30)}}
    if maybe(0.4):
        c['AdditionalCriteria'] = group()
    if maybe(0.4):
        c['EndStrategy'] = rng.choice([
            {'DateOffset': {'DateField': rng.choice(['StartDate', 'EndDate']), 'Offset': rng.randint(0, 365)}},
            {'CustomEra': {'DrugCodesetId': rng.choice([0, 1] + ([] if CLEAN else [None])) if not edge(0.2) else None,
                           'GapDays': rng.randint(0, 30), 'Offset': rng.randint(0, 30),
                           **({'DaysSupplyOverride': rng.randint(1, 90)} if maybe(0.3) else {})}}])
    if maybe(0.3):
        c['CensoringCriteria'] = [criteria() for _ in range(rng.randint(1, 2))]
    if maybe(0.2):
        c['CensorWindow'] = period()
    if edge(0.02):
        c[rng.choice(list(c))] = None
    return c


for _ in range(N_FUZZ):
    add_cohort(json.dumps(cohort()), [rng.choice(OPTIONS)])

with open(OUT, 'w') as f:
    for op, text, o in cases:
        f.write(f'{op}\t{b(text)}\t{b(o) if o else ""}\n')
print(len(cases), 'cases')
