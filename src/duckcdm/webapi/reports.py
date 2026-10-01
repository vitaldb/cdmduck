"""Data source reports (port of WebAPI CDMResultsAnalysisRunner): Achilles results -> the JSON the ATLAS UI expects."""
import datetime
import decimal
import os

from ..achilles import run_achilles
from ..sqlrender import render
from .sources import sql_resource

_DIR = os.path.join(os.path.dirname(__file__), 'sql', 'cdmresults')


def _camel(col):
    """GenericRowMapper.snakeCaseToCamelCase."""
    parts = col.lower().split('_')
    s = ''.join(p.capitalize() for p in parts)
    return s[:1].lower() + s[1:] if s else s


def _json_value(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, datetime.datetime):
        return int(v.timestamp() * 1000)
    if isinstance(v, datetime.date):
        return int(datetime.datetime(v.year, v.month, v.day).timestamp() * 1000)
    return v


def _get(row, key):
    """Case-insensitive column lookup (like Java ResultSet.getXxx(label))."""
    for k, v in row.items():
        if k.lower() == key.lower():
            return v
    return None


def _int(v):
    return int(v) if v is not None else 0


def _float(v):
    return float(v) if v is not None else 0.0


# Java row mappers (primitive fields: null -> 0)
MAPPERS = {
    'attribute': lambda r: {'attributeName': _get(r, 'ATTRIBUTE_NAME'), 'attributeValue': _get(r, 'ATTRIBUTE_VALUE')},
    'distribution': lambda r: {'intervalIndex': _int(_get(r, 'INTERVALINDEX')),
                               'percentValue': _float(_get(r, 'PERCENTVALUE')),
                               'countValue': _int(_get(r, 'COUNTVALUE'))},
    'count': lambda r: {'conditionConceptName': None, 'conditionConceptId': 0, 'observationConceptName': None,
                        'observationConceptId': 0, 'conceptName': _get(r, 'CONCEPTNAME'),
                        'conceptId': _int(_get(r, 'CONCEPTID')), 'countValue': _int(_get(r, 'COUNTVALUE'))},
    'cumulative': lambda r: {'seriesName': _get(r, 'SERIESNAME'),
                             'xLengthOfObservation': _int(_get(r, 'XLENGTHOFOBSERVATION')),
                             'yPercentPersons': _float(_get(r, 'YPERCENTPERSONS'))},
    'month': lambda r: {'monthYear': _int(_get(r, 'MONTHYEAR')), 'percentValue': _float(_get(r, 'PERCENTVALUE')),
                        'countValue': _int(_get(r, 'COUNTVALUE'))},
    'stats': lambda r: {'minValue': _int(_get(r, 'MINVALUE')), 'maxValue': _int(_get(r, 'MAXVALUE')),
                        'intervalSize': _int(_get(r, 'INTERVALSIZE'))},
    'quartile': lambda r: {'category': _get(r, 'CATEGORY'), 'conceptId': 0,
                           'p10Value': _int(_get(r, 'P10_VALUE')), 'p25Value': _int(_get(r, 'P25_VALUE')),
                           'p75Value': _int(_get(r, 'P75_VALUE')), 'p90Value': _int(_get(r, 'P90_VALUE')),
                           'minValue': _int(_get(r, 'MIN_VALUE')), 'medianValue': _int(_get(r, 'MEDIAN_VALUE')),
                           'maxValue': _int(_get(r, 'MAX_VALUE'))},
    'series': lambda r: {'seriesName': _get(r, 'SERIES_NAME'), 'xCalendarMonth': _int(_get(r, 'X_CALENDAR_MONTH')),
                         'yRecordCount': _float(_get(r, 'Y_RECORD_COUNT'))},
    'decile': lambda r: {'numPersons': 0, 'trellisName': _get(r, 'TRELLIS_NAME'), 'conceptId': 0,
                         'seriesName': _get(r, 'SERIES_NAME'),
                         'yPrevalence1000Pp': _float(_get(r, 'Y_PREVALENCE_1000PP')),
                         'xCalendarYear': _int(_get(r, 'X_CALENDAR_YEAR'))},
    'prevalence': lambda r: {'xCalendarMonth': _int(_get(r, 'X_CALENDAR_MONTH')), 'conceptId': 0,
                             'conceptName': None, 'yPrevalence1000Pp': _float(_get(r, 'Y_PREVALENCE_1000PP')),
                             'numPersons': 0},
    'generic': lambda r: {_camel(k): _json_value(v) for k, v in r.items()},
}

DASHBOARD = [('summary', 'person/population.sql', 'attribute'),
             ('ageAtFirstObservation', 'observationperiod/ageatfirst.sql', 'distribution'),
             ('gender', 'person/gender.sql', 'count'),
             ('cumulativeObservation', 'observationperiod/cumulativeduration.sql', 'cumulative'),
             ('observedByMonth', 'observationperiod/observedbymonth.sql', 'month')]
PERSON = [('summary', 'person/population.sql', 'attribute'), ('gender', 'person/gender.sql', 'count'),
          ('race', 'person/race.sql', 'count'), ('ethnicity', 'person/ethnicity.sql', 'count'),
          ('yearOfBirth', 'person/yearofbirth_data.sql', 'distribution'),
          ('yearOfBirthStats', 'person/yearofbirth_stats.sql', 'stats')]
DATA_DENSITY = [('conceptsPerPerson', 'datadensity/conceptsperperson.sql', 'quartile'),
                ('recordsPerPerson', 'datadensity/recordsperperson.sql', 'series'),
                ('totalRecords', 'datadensity/totalrecords.sql', 'series')]
DEATH = [('prevalenceByGenderAgeYear', 'death/sqlPrevalenceByGenderAgeYear.sql', 'decile'),
         ('prevalenceByMonth', 'death/sqlPrevalenceByMonth.sql', 'prevalence'),
         ('deathByType', 'death/sqlDeathByType.sql', 'count'),
         ('ageAtDeath', 'death/sqlAgeAtDeath.sql', 'quartile')]
OBSERVATION_PERIOD = [
    ('ageAtFirst', 'observationperiod/ageatfirst.sql', 'distribution'),
    ('observationLength', 'observationperiod/observationlength_data.sql', 'distribution'),
    ('observationLengthStats', 'observationperiod/observationlength_stats.sql', 'stats'),
    ('personsWithContinuousObservationsByYearStats', 'observationperiod/observedbyyear_stats.sql', 'stats'),
    ('personsWithContinuousObservationsByYear', 'observationperiod/observedbyyear_data.sql', 'distribution'),
    ('ageByGender', 'observationperiod/agebygender.sql', 'quartile'),
    ('durationByGender', 'observationperiod/observationlengthbygender.sql', 'quartile'),
    ('durationByAgeDecile', 'observationperiod/observationlengthbyage.sql', 'quartile'),
    ('cumulativeObservation', 'observationperiod/cumulativeduration.sql', 'cumulative'),
    ('observedByMonth', 'observationperiod/observedbymonth.sql', 'month'),
    ('observationPeriodsPerPerson', 'observationperiod/periodsperperson.sql', 'count')]


class Reports:
    def __init__(self, source):
        self.s = source

    # -- preparation
    def ready(self):
        cur = self.s.cursor()
        n = cur.execute("select count(*) from information_schema.tables where table_schema = ? and "
                        "table_name = 'achilles_results'", [self.s.results_schema]).fetchone()[0]
        return n > 0

    def build(self, log=None, resume=False, skip=()):
        """Achilles analyses + concept_hierarchy. Repopulates the results schema."""
        s = self.s
        with s.lock:
            cur = s.cursor()
            name = s.name
            try:
                name = cur.execute(f'select cdm_source_name from {s.cdm_schema}.cdm_source limit 1').fetchone()[0] or name
            except Exception:
                pass
            done, failed = run_achilles(cur, s.cdm_schema, s.results_schema, s.vocab_schema, source_name=name,
                                        log=log, resume=resume, skip=skip)
            ddl = sql_resource('results', 'concept_hierarchy.sql')
            body = ddl[ddl.index('CREATE TABLE'):].replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1)
            s.execute_script(render(body, results_schema=s.results_schema), cur)
            s.execute_script(render(sql_resource('results', 'init_concept_hierarchy.sql'),
                                    results_schema=s.results_schema, vocab_schema=s.vocab_schema), cur)
        return done, failed

    # -- queries
    def _rows(self, rel, concept_id=None):
        s = self.s
        with open(os.path.join(_DIR, rel), encoding='utf-8') as f:
            sql = f.read()
        params = dict(results_database_schema=s.results_schema, vocab_database_schema=s.vocab_schema,
                      cdm_database_schema=s.cdm_schema)
        if concept_id is not None:
            params['conceptId'] = int(concept_id)
        return s.query_dicts(sql, **params)

    def _bundle(self, spec):
        return {key: [MAPPERS[m](r) for r in self._rows(rel)] for key, rel, m in spec}

    def dashboard(self):
        return self._bundle(DASHBOARD)

    def person(self):
        return self._bundle(PERSON)

    def data_density(self):
        return self._bundle(DATA_DENSITY)

    def death(self):
        return self._bundle(DEATH)

    def observation_period(self):
        return self._bundle(OBSERVATION_PERIOD)

    def has_domain(self, domain):
        return os.path.isfile(os.path.join(_DIR, domain.lower(), 'treemap.sql'))

    def treemap(self, domain):
        return [MAPPERS['generic'](r) for r in self._rows(os.path.join(domain.lower(), 'treemap.sql'))]

    def drilldown(self, domain, concept_id=None):
        folder = os.path.join(domain.lower(), 'drilldown' if concept_id is not None else 'drilldownsummary')
        out = {}
        for name in sorted(os.listdir(os.path.join(_DIR, folder))):
            if name.endswith('.sql'):
                out[name[:-4]] = [MAPPERS['generic'](r) for r in self._rows(os.path.join(folder, name), concept_id)]
        return out
