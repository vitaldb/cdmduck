"""org.ohdsi.circe.cohortdefinition.builders port."""
import os

from ._jutil import (CirceError, commons_split, i32, java_parse_int, jformat_f, jjoin, jreplace, jstr, nn)
from ._model import COLUMN_NAME

_SQL_DIR = os.path.join(os.path.dirname(__file__), 'sql')
_cache = {}


def template(name):
    t = _cache.get(name)
    if t is None:
        with open(os.path.join(_SQL_DIR, name), encoding='utf-8', newline='') as f:
            t = _cache[name] = f.read()
    return t


# ---- BuilderUtils ------------------------------------------------------------

def get_date_adjustment_expression(da, start_column, end_column):
    e = jreplace(template('dateAdjustment.sql'), '@startOffset', str(da.start_offset))
    e = jreplace(e, '@startColumn', start_column)
    e = jreplace(e, '@endOffset', str(da.end_offset))
    return jreplace(e, '@endColumn', end_column)


def _codeset_join(alias, column, codeset_id):
    return 'JOIN #Codesets %s on (%s = %s.concept_id and %s.codeset_id = %s)' % (
        alias, jstr(column), alias, alias, jstr(codeset_id))


def get_codeset_join_expression(standard_id, standard_col, source_id, source_col):
    clauses = []
    if standard_id is not None:
        clauses.append(_codeset_join('cs', standard_col, standard_id))
    if source_id is not None:
        clauses.append(_codeset_join('cns', source_col, source_id))
    return '\n'.join(clauses) if clauses else ''


def get_codeset_in_expression(column, sel):
    if sel.codeset_id is not None:
        return '%s %s in (select concept_id from #Codesets where codeset_id = %s)' % (
            column, 'not' if sel.is_exclusion else '', jstr(sel.codeset_id))
    return '%s is %s null' % (column, '' if sel.is_exclusion else 'not')


_OPS = {'lt': '<', 'lte': '<=', 'eq': '=', '!eq': '<>', 'gt': '>', 'gte': '>='}


def get_operator(op):
    if op is None:
        raise CirceError('NullPointerException: switch on null')
    if op in _OPS:
        return _OPS[op]
    raise CirceError('RuntimeException: Unknown operator type: ' + op)


def date_string_to_sql(date):
    parts = commons_split(nn(date, 'date'), '-')
    if len(parts) < 3:
        # calls Integer.valueOf in order and goes out of range
        for p in parts:
            java_parse_int(p)
        raise CirceError('ArrayIndexOutOfBoundsException')
    y, m, d = java_parse_int(parts[0]), java_parse_int(parts[1]), java_parse_int(parts[2])
    return 'DATEFROMPARTS(%d, %d, %d)' % (y, m, d)


def build_date_range_clause(expr, rng):
    op = nn(rng.op, 'op')
    if op.endswith('bt'):
        return '%s(%s >= %s and %s <= %s)' % ('not ' if op.startswith('!') else '', expr,
                                               date_string_to_sql(rng.value), expr, date_string_to_sql(rng.extent))
    return '%s %s %s' % (expr, get_operator(rng.op), date_string_to_sql(rng.value))


def build_numeric_range_clause(expr, rng, fmt=None):
    op = nn(rng.op, 'op')
    if fmt is None:
        iv = lambda n: str(nn(n, 'value').int_value())
    else:
        prec = int(fmt[1:-1])
        iv = lambda n: jformat_f(nn(n, 'value').double_value(), prec)
    if op.endswith('bt'):
        return '%s(%s >= %s and %s <= %s)' % ('not ' if op.startswith('!') else '', expr, iv(rng.value), expr,
                                               iv(rng.extent))
    return '%s %s %s' % (expr, get_operator(rng.op), iv(rng.value))


def concept_ids(concepts):
    return [nn(c, 'concept').concept_id for c in concepts]


def in_concepts(concepts):
    return jjoin(concept_ids(concepts), ',')


def build_text_filter_clause(expr, f):
    op = nn(f.op, 'op')
    negation = 'not' if op.startswith('!') else ''
    prefix = '%' if op.endswith('endsWith') or op.endswith('contains') else ''
    postfix = '%' if op.endswith('startsWith') or op.endswith('contains') else ''
    value = f.text
    if value:
        # value.replaceAll("\\\\*\\'", "''")
        import re
        value = re.sub(r"\\*'", "''", value)
    return "%s %s like '%s%s%s'" % (expr, negation, prefix, jstr(value), postfix)


def split_in_clause(column, values, group_size):
    groups = [values[i:i + group_size] for i in range(0, len(values), group_size)]
    return '(%s)' % ' or '.join('%s in (%s)' % (column, jjoin(g, ',')) for g in groups)


def _has(arr):
    return arr is not None and len(arr) > 0


def column_name(col):
    return COLUMN_NAME[col]


# ---- CriteriaSqlBuilder ------------------------------------------------------

class CriteriaSqlBuilder:
    TEMPLATE = None
    DEFAULT_COLUMNS = ()
    DEFAULT_SELECT = ()
    TABLE_COLUMNS = {}
    LABEL = ''

    def get_criteria_sql(self, c, options=None):
        query = template(self.TEMPLATE)
        query = self.embed_codeset_clause(query, c)
        selects = self.resolve_select_clauses(c)
        joins = self.resolve_join_clauses(c)
        wheres = self.resolve_where_clauses(c)
        query = self.embed_ordinal_expression(query, c, wheres)
        query = jreplace(query, '@selectClause', jjoin(selects, ','))
        query = jreplace(query, '@joinClause', jjoin(joins, '\n'))
        query = jreplace(query, '@whereClause', ('WHERE ' + jjoin(wheres, '\nAND ')) if wheres else '')
        if options is not None:
            cols = [col for col in options.additional_columns if col not in self.DEFAULT_COLUMNS]
            if cols:
                query = jreplace(query, '@additionalColumns', ', ' + self.get_additional_columns(cols))
            else:
                query = jreplace(query, '@additionalColumns', '')
        else:
            query = jreplace(query, '@additionalColumns', '')
        return query

    def table_column(self, col):
        if col is None:
            raise CirceError('NullPointerException: switch on null')
        if col in self.TABLE_COLUMNS:
            return self.TABLE_COLUMNS[col]
        raise CirceError('IllegalArgumentException: Invalid CriteriaColumn for %s:%s' % (self.LABEL, col))

    def get_additional_columns(self, cols):
        return ', '.join('%s as %s' % (self.table_column(c), column_name(c)) for c in cols)

    def embed_codeset_clause(self, query, c):
        return query

    def embed_ordinal_expression(self, query, c, wheres):
        return query

    def resolve_select_clauses(self, c):
        return []

    def resolve_join_clauses(self, c):
        return []

    def resolve_where_clauses(self, c):
        wheres = []
        if c.date_adjustment is not None:
            wheres.append('C.end_date >= C.start_date')
        return wheres

    # common fragments
    def _ordinal(self, query, c, wheres, expr):
        if c.first is not None and c.first:
            wheres.append('C.ordinal = 1')
            return jreplace(query, '@ordinalExpression', expr)
        return jreplace(query, '@ordinalExpression', '')

    def _dates(self, c, start_expr, end_expr, plain):
        if c.date_adjustment is not None:
            da = c.date_adjustment
            return get_date_adjustment_expression(da, start_expr if da.start_with == 'START_DATE' else end_expr,
                                                  start_expr if da.end_with == 'START_DATE' else end_expr)
        return plain


PERSON_JOIN = 'JOIN @cdm_database_schema.PERSON P on C.person_id = P.person_id'
VISIT_JOIN = ('JOIN @cdm_database_schema.VISIT_OCCURRENCE V on C.visit_occurrence_id = V.visit_occurrence_id '
              'and C.person_id = V.person_id')
PROVIDER_JOIN = 'LEFT JOIN @cdm_database_schema.PROVIDER PR on C.provider_id = PR.provider_id'
SE = {'START_DATE', 'END_DATE'}
SEV = {'START_DATE', 'END_DATE', 'VISIT_ID'}


def _codeset_where(alias_col, codeset_id):
    if codeset_id is None:
        return ''
    return 'where %s in (SELECT concept_id from  #Codesets where codeset_id = %s)' % (alias_col, jstr(codeset_id))


def _std_person_join(c, age_fields=('age',)):
    return any(getattr(c, f) is not None for f in age_fields) or _has(getattr(c, 'gender', None)) \
        or c.gender_cs is not None


def _psv_joins(c, joins):
    if _has(c.visit_type) or c.visit_type_cs is not None:
        joins.append(VISIT_JOIN)
    if _has(c.provider_specialty) or c.provider_specialty_cs is not None:
        joins.append(PROVIDER_JOIN)


def _age_gender_where(c, w, age_expr='YEAR(C.start_date) - P.year_of_birth'):
    if c.age is not None:
        w.append(build_numeric_range_clause(age_expr, c.age))
    if _has(c.gender):
        w.append('P.gender_concept_id in (%s)' % in_concepts(c.gender))
    if c.gender_cs is not None:
        w.append(get_codeset_in_expression('P.gender_concept_id', c.gender_cs))


def _ps_visit_where(c, w):
    if _has(c.provider_specialty):
        w.append('PR.specialty_concept_id in (%s)' % in_concepts(c.provider_specialty))
    if c.provider_specialty_cs is not None:
        w.append(get_codeset_in_expression('PR.specialty_concept_id', c.provider_specialty_cs))
    if _has(c.visit_type):
        w.append('V.visit_concept_id in (%s)' % in_concepts(c.visit_type))
    if c.visit_type_cs is not None:
        w.append(get_codeset_in_expression('V.visit_concept_id', c.visit_type_cs))


def _era_age_gender_where(c, w):
    if c.age_at_start is not None:
        w.append(build_numeric_range_clause('YEAR(C.start_date) - P.year_of_birth', c.age_at_start))
    if c.age_at_end is not None:
        w.append(build_numeric_range_clause('YEAR(C.end_date) - P.year_of_birth', c.age_at_end))
    if _has(c.gender):
        w.append('P.gender_concept_id in (%s)' % in_concepts(c.gender))
    if c.gender_cs is not None:
        w.append(get_codeset_in_expression('P.gender_concept_id', c.gender_cs))


def _type_in(col, concepts, exclude):
    return '%s %s in (%s)' % (col, 'not' if exclude else '', in_concepts(concepts))


class ConditionEraSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'conditionEra.sql'
    LABEL = 'Condition Era'
    DEFAULT_COLUMNS = SE
    DEFAULT_SELECT = ['ce.person_id', 'ce.condition_era_id', 'ce.condition_concept_id', 'ce.condition_occurrence_count']
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.condition_concept_id', 'ERA_OCCURRENCES': 'C.condition_occurrence_count',
                     'DURATION': '(DATEDIFF(d,C.start_date, C.end_date))'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', _codeset_where('ce.condition_concept_id', c.codeset_id))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY ce.person_id ORDER BY '
                                      'ce.condition_era_start_date, ce.condition_era_id) as ordinal')

    def resolve_select_clauses(self, c):
        return list(self.DEFAULT_SELECT) + [self._dates(
            c, 'ce.condition_era_start_date', 'ce.condition_era_end_date',
            'ce.condition_era_start_date as start_date, ce.condition_era_end_date as end_date')]

    def resolve_join_clauses(self, c):
        return [PERSON_JOIN] if _std_person_join(c, ('age_at_start', 'age_at_end')) else []

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.era_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.era_start_date))
        if c.era_end_date is not None:
            w.append(build_date_range_clause('C.end_date', c.era_end_date))
        if c.occurrence_count is not None:
            w.append(build_numeric_range_clause('C.condition_occurrence_count', c.occurrence_count))
        if c.era_length is not None:
            w.append(build_numeric_range_clause('DATEDIFF(d,C.start_date, C.end_date)', c.era_length))
        _era_age_gender_where(c, w)
        return w


class ConditionOccurrenceSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'conditionOccurrence.sql'
    LABEL = 'Condition Occurrence'
    DEFAULT_COLUMNS = SEV
    DEFAULT_SELECT = ['co.person_id', 'co.condition_occurrence_id', 'co.condition_concept_id', 'co.visit_occurrence_id']
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.condition_concept_id', 'DURATION': '(DATEDIFF(d,C.start_date, C.end_date))'}
    END = 'COALESCE(co.condition_end_date, DATEADD(day,1,co.condition_start_date))'

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', get_codeset_join_expression(
            c.codeset_id, 'co.condition_concept_id', c.condition_source_concept, 'co.condition_source_concept_id'))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY co.person_id ORDER BY '
                                      'co.condition_start_date, co.condition_occurrence_id) as ordinal')

    def resolve_select_clauses(self, c):
        s = list(self.DEFAULT_SELECT)
        if _has(c.condition_type) or c.condition_type_cs is not None:
            s.append('co.condition_type_concept_id')
        if c.stop_reason is not None:
            s.append('co.stop_reason')
        if _has(c.provider_specialty) or c.provider_specialty_cs is not None:
            s.append('co.provider_id')
        if _has(c.condition_status) or c.condition_status_cs is not None:
            s.append('co.condition_status_concept_id')
        s.append(self._dates(c, 'co.condition_start_date', self.END,
                             'co.condition_start_date as start_date, ' + self.END + ' as end_date'))
        return s

    def resolve_join_clauses(self, c):
        j = [PERSON_JOIN] if _std_person_join(c) else []
        _psv_joins(c, j)
        return j

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.occurrence_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.occurrence_start_date))
        if c.occurrence_end_date is not None:
            w.append(build_date_range_clause('C.end_date', c.occurrence_end_date))
        if _has(c.condition_type):
            w.append(_type_in('C.condition_type_concept_id', c.condition_type, bool(c.condition_type_exclude)))
        if c.condition_type_cs is not None:
            w.append(get_codeset_in_expression('C.condition_type_concept_id', c.condition_type_cs))
        if c.stop_reason is not None:
            w.append(build_text_filter_clause('C.stop_reason', c.stop_reason))
        _age_gender_where(c, w)
        _ps_visit_where(c, w)
        if _has(c.condition_status):
            w.append('C.condition_status_concept_id in (%s)' % in_concepts(c.condition_status))
        if c.condition_status_cs is not None:
            w.append(get_codeset_in_expression('C.condition_status_concept_id', c.condition_status_cs))
        return w


class CustomEraSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'customEra.sql'
    LABEL = 'Custom Era'
    DEFAULT_COLUMNS = SEV
    TABLE_COLUMNS = {'DURATION': 'DATEDIFF(d, C.start_date, C.end_date)'}

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY E.person_id ORDER BY E.start_date, '
                                      'E.end_date) as ordinal')

    def resolve_select_clauses(self, c):
        return ['E.person_id', 'row_number() over (ORDER BY E.person_id, E.start_date, E.end_date) as event_id',
                self._dates(c, 'E.start_date', 'E.end_date', 'E.start_date as start_date, E.end_date as end_date')]

    def resolve_join_clauses(self, c):
        return [PERSON_JOIN] if c.age_at_start is not None or c.gender_cs is not None else []

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.start_date))
        if c.end_date is not None:
            w.append(build_date_range_clause('C.end_date', c.end_date))
        if c.age_at_start is not None:
            w.append(build_numeric_range_clause('YEAR(C.start_date) - P.year_of_birth', c.age_at_start))
        if c.gender_cs is not None:
            w.append(get_codeset_in_expression('P.gender_concept_id', c.gender_cs))
        if c.duration is not None:
            w.append(build_numeric_range_clause('DATEDIFF(d,C.start_date, C.end_date)', c.duration))
        return w

    def get_criteria_sql_with(self, c, options, criteria_query):
        q = super().get_criteria_sql(c, options)
        q = jreplace(q, '@eraconstructorpad', str(0 if c.gap_days is None else c.gap_days))
        return jreplace(q, '@criteriaQueries', criteria_query)


class DeathSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'death.sql'
    LABEL = 'Death'
    DEFAULT_COLUMNS = SEV
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'coalesce(C.cause_concept_id,0)', 'DURATION': 'CAST(1 as int)'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', get_codeset_join_expression(
            c.codeset_id, 'd.cause_concept_id', c.death_source_concept, 'd.cause_source_concept_id'))

    def resolve_select_clauses(self, c):
        s = ['d.person_id', 'd.cause_concept_id']
        if _has(c.death_type) or c.death_type_cs is not None:
            s.append('d.death_type_concept_id')
        s.append(self._dates(c, 'd.death_date', 'DATEADD(day,1,d.death_date)', None)
                 if c.date_adjustment is not None else
                 'd.death_date as start_date, DATEADD(day,1,d.death_date) as end_date')
        return s

    def _dates(self, c, start_expr, end_expr, plain):
        # Death passes (death_date, death_date+1) regardless of the start/end choice
        return get_date_adjustment_expression(c.date_adjustment, start_expr, end_expr)

    def resolve_join_clauses(self, c):
        return [PERSON_JOIN] if _std_person_join(c) else []

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.occurrence_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.occurrence_start_date))
        if _has(c.death_type):
            w.append(_type_in('C.death_type_concept_id', c.death_type, c.death_type_exclude))
        if c.death_type_cs is not None:
            w.append(get_codeset_in_expression('C.death_type_concept_id', c.death_type_cs))
        _age_gender_where(c, w)
        return w


class DeviceExposureSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'deviceExposure.sql'
    LABEL = 'Device Exposure'
    DEFAULT_COLUMNS = SEV
    DEFAULT_SELECT = ['de.person_id', 'de.device_exposure_id', 'de.device_concept_id', 'de.visit_occurrence_id',
                      'de.quantity']
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.device_concept_id', 'QUANTITY': 'C.quantity',
                     'DURATION': 'DATEDIFF(d,c.start_date, c.end_date)'}
    END = 'COALESCE(de.device_exposure_end_date, DATEADD(day,1,de.device_exposure_start_date))'

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', get_codeset_join_expression(
            c.codeset_id, 'de.device_concept_id', c.device_source_concept, 'de.device_source_concept_id'))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY de.person_id ORDER BY '
                                      'de.device_exposure_start_date, de.device_exposure_id) as ordinal')

    def resolve_select_clauses(self, c):
        s = list(self.DEFAULT_SELECT)
        if _has(c.device_type) or c.device_type_cs is not None:
            s.append('de.device_type_concept_id')
        if c.unique_device_id is not None:
            s.append('de.unique_device_id')
        if _has(c.provider_specialty) or c.provider_specialty_cs is not None:
            s.append('de.provider_id')
        s.append(self._dates(c, 'de.device_exposure_start_date', self.END,
                             'de.device_exposure_start_date as start_date, ' + self.END + ' as end_date'))
        return s

    def resolve_join_clauses(self, c):
        j = [PERSON_JOIN] if _std_person_join(c) else []
        _psv_joins(c, j)
        return j

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.occurrence_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.occurrence_start_date))
        if c.occurrence_end_date is not None:
            w.append(build_date_range_clause('C.end_date', c.occurrence_end_date))
        if _has(c.device_type):
            w.append(_type_in('C.device_type_concept_id', c.device_type, c.device_type_exclude))
        if c.device_type_cs is not None:
            w.append(get_codeset_in_expression('C.device_type_concept_id', c.device_type_cs))
        if c.unique_device_id is not None:
            w.append(build_text_filter_clause('C.unique_device_id', c.unique_device_id))
        if c.quantity is not None:
            w.append(build_numeric_range_clause('C.quantity', c.quantity))
        _age_gender_where(c, w)
        _ps_visit_where(c, w)
        return w


class DoseEraSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'doseEra.sql'
    LABEL = 'Device Exposure'
    DEFAULT_COLUMNS = SEV
    DEFAULT_SELECT = ['de.person_id', 'de.dose_era_id', 'de.drug_concept_id, de.unit_concept_id, de.dose_value']
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.drug_concept_id', 'DURATION': 'DATEDIFF(d, C.start_date, C.end_date)',
                     'UNIT': 'C.unit_concept_id', 'VALUE_AS_NUMBER': 'C.dose_value'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', _codeset_where('de.drug_concept_id', c.codeset_id))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY de.person_id ORDER BY '
                                      'de.dose_era_start_date, de.dose_era_id) as ordinal')

    def resolve_join_clauses(self, c):
        return [PERSON_JOIN] if _std_person_join(c, ('age_at_start', 'age_at_end')) else []

    def resolve_select_clauses(self, c):
        return list(self.DEFAULT_SELECT) + [self._dates(
            c, 'de.dose_era_start_date', 'de.dose_era_end_date',
            'de.dose_era_start_date as start_date, de.dose_era_end_date as end_date')]

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.era_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.era_start_date))
        if c.era_end_date is not None:
            w.append(build_date_range_clause('C.end_date', c.era_end_date))
        if _has(c.unit):
            w.append('c.unit_concept_id in (%s)' % in_concepts(c.unit))
        if c.unit_cs is not None:
            w.append(get_codeset_in_expression('c.unit_concept_id', c.unit_cs))
        if c.dose_value is not None:
            w.append(build_numeric_range_clause('c.dose_value', c.dose_value, '.4f'))
        if c.era_length is not None:
            w.append(build_numeric_range_clause('DATEDIFF(d,C.start_date, C.end_date)', c.era_length))
        _era_age_gender_where(c, w)
        return w


class DrugEraSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'drugEra.sql'
    LABEL = 'Drug Era'
    DEFAULT_COLUMNS = SEV
    DEFAULT_SELECT = ['de.person_id', 'de.drug_era_id', 'de.drug_concept_id', 'de.drug_exposure_count', 'de.gap_days']
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.drug_concept_id', 'ERA_OCCURRENCES': 'C.drug_exposure_count',
                     'GAP_DAYS': 'C.gap_days', 'DURATION': 'DATEDIFF(d,C.start_date, C.end_date)'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', _codeset_where('de.drug_concept_id', c.codeset_id))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY de.person_id ORDER BY '
                                      'de.drug_era_start_date, de.drug_era_id) as ordinal')

    def resolve_select_clauses(self, c):
        return list(self.DEFAULT_SELECT) + [self._dates(
            c, 'de.drug_era_start_date', 'de.drug_era_end_date',
            'de.drug_era_start_date as start_date, de.drug_era_end_date as end_date')]

    def resolve_join_clauses(self, c):
        return [PERSON_JOIN] if _std_person_join(c, ('age_at_start', 'age_at_end')) else []

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.era_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.era_start_date))
        if c.era_end_date is not None:
            w.append(build_date_range_clause('C.end_date', c.era_end_date))
        if c.occurrence_count is not None:
            w.append(build_numeric_range_clause('C.drug_exposure_count', c.occurrence_count))
        if c.era_length is not None:
            w.append(build_numeric_range_clause('DATEDIFF(d,C.start_date, C.end_date)', c.era_length))
        if c.gap_days is not None:
            w.append(build_numeric_range_clause('C.gap_days', c.gap_days))
        _era_age_gender_where(c, w)
        return w


class DrugExposureSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'drugExposure.sql'
    LABEL = 'Drug Exposure'
    DEFAULT_COLUMNS = SEV
    DEFAULT_SELECT = ['de.person_id', 'de.drug_exposure_id', 'de.drug_concept_id', 'de.visit_occurrence_id',
                      'days_supply', 'quantity', 'refills']
    TABLE_COLUMNS = {'DAYS_SUPPLY': 'C.days_supply', 'DOMAIN_CONCEPT': 'C.drug_concept_id',
                     'DURATION': 'DATEDIFF(d, C.start_date, C.end_date)', 'QUANTITY': 'C.quantity',
                     'REFILLS': 'C.refills'}
    END = ('COALESCE(de.drug_exposure_end_date, DATEADD(day,de.days_supply,de.drug_exposure_start_date), '
           'DATEADD(day,1,de.drug_exposure_start_date))')

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', get_codeset_join_expression(
            c.codeset_id, 'de.drug_concept_id', c.drug_source_concept, 'de.drug_source_concept_id'))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY de.person_id ORDER BY '
                                      'de.drug_exposure_start_date, de.drug_exposure_id) as ordinal')

    def resolve_select_clauses(self, c):
        s = list(self.DEFAULT_SELECT)
        if _has(c.drug_type) or c.drug_type_cs is not None:
            s.append('de.drug_type_concept_id')
        if c.stop_reason is not None:
            s.append('de.stop_reason')
        if _has(c.route_concept) or c.route_concept_cs is not None:
            s.append('de.route_concept_id')
        if _has(c.dose_unit) or c.dose_unit_cs is not None:
            s.append('de.dose_unit_concept_id')
        if c.lot_number is not None:
            s.append('de.lot_number')
        if _has(c.provider_specialty) or c.provider_specialty_cs is not None:
            s.append('de.provider_id')
        s.append(self._dates(c, 'de.drug_exposure_start_date', self.END,
                             'de.drug_exposure_start_date as start_date, ' + self.END + ' as end_date'))
        return s

    def resolve_join_clauses(self, c):
        j = [PERSON_JOIN] if _std_person_join(c) else []
        _psv_joins(c, j)
        return j

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.occurrence_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.occurrence_start_date))
        if c.occurrence_end_date is not None:
            w.append(build_date_range_clause('C.start_date', c.occurrence_end_date))   # as in the Java original (start_date)
        if _has(c.drug_type):
            w.append(_type_in('C.drug_type_concept_id', c.drug_type, c.drug_type_exclude))
        if c.drug_type_cs is not None:
            w.append(get_codeset_in_expression('C.drug_type_concept_id', c.drug_type_cs))
        if c.stop_reason is not None:
            w.append(build_text_filter_clause('C.stop_reason', c.stop_reason))
        if c.refills is not None:
            w.append(build_numeric_range_clause('C.refills', c.refills))
        if c.quantity is not None:
            w.append(build_numeric_range_clause('C.quantity', c.quantity, '.4f'))
        if c.days_supply is not None:
            w.append(build_numeric_range_clause('C.days_supply', c.days_supply))
        if _has(c.route_concept):
            w.append('C.route_concept_id in (%s)' % in_concepts(c.route_concept))
        if c.route_concept_cs is not None:
            w.append(get_codeset_in_expression('C.route_concept_id', c.route_concept_cs))
        if _has(c.dose_unit):
            w.append('C.dose_unit_concept_id in (%s)' % in_concepts(c.dose_unit))
        if c.dose_unit_cs is not None:
            w.append(get_codeset_in_expression('C.dose_unit_concept_id', c.dose_unit_cs))
        if c.lot_number is not None:
            w.append(build_text_filter_clause('C.lot_number', c.lot_number))
        _age_gender_where(c, w)
        _ps_visit_where(c, w)
        return w


class EpisodeSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'episode.sql'
    LABEL = 'Episode'
    DEFAULT_COLUMNS = {'START_DATE', 'END_DATE', 'DOMAIN_CONCEPT'}
    DEFAULT_SELECT = ['ep.person_id', 'ep.episode_id', 'ep.episode_concept_id', 'ep.episode_number',
                      'ep.episode_object_concept_id', 'ep.episode_type_concept_id']
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.episode_concept_id', 'DURATION': 'DATEDIFF(d, C.start_date, C.end_date)'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', get_codeset_join_expression(c.codeset_id, 'ep.episode_concept_id',
                                                                         None, None))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY ep.person_id ORDER BY '
                                      'ep.episode_start_date, ep.episode_id) as ordinal')

    def resolve_select_clauses(self, c):
        return list(self.DEFAULT_SELECT) + [self._dates(
            c, 'ep.episode_start_date', 'ep.episode_end_date',
            'ep.episode_start_date as start_date, ep.episode_end_date as end_date')]

    def resolve_join_clauses(self, c):
        return [PERSON_JOIN] if c.age is not None or c.gender_cs is not None else []

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.episode_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.episode_start_date))
        if c.episode_end_date is not None:
            w.append(build_date_range_clause('C.end_date', c.episode_end_date))
        if c.episode_number is not None:
            w.append(build_numeric_range_clause('C.episode_number', c.episode_number))
        if c.age is not None:
            w.append(build_numeric_range_clause('YEAR(C.start_date) - P.year_of_birth', c.age))
        if c.gender_cs is not None:
            w.append(get_codeset_in_expression('P.gender_concept_id', c.gender_cs))
        if c.episode_object_concept_cs is not None:
            w.append(get_codeset_in_expression('C.episode_object_concept_id', c.episode_object_concept_cs))
        if c.episode_type_cs is not None:
            w.append(get_codeset_in_expression('C.episode_type_concept_id', c.episode_type_cs))
        return w


class LocationRegionSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'locationRegion.sql'
    LABEL = 'Location Region'
    DEFAULT_COLUMNS = SEV
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.region_concept_id'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', get_codeset_join_expression(c.codeset_id, 'l.region_concept_id',
                                                                         None, None))

    def resolve_where_clauses(self, c):
        return []


class MeasurementSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'measurement.sql'
    LABEL = 'Measurement'
    DEFAULT_COLUMNS = SEV
    DEFAULT_SELECT = ['m.person_id', 'm.measurement_id', 'm.measurement_concept_id', 'm.visit_occurrence_id',
                      'm.value_as_number', 'm.range_high', 'm.range_low']
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.measurement_concept_id', 'DURATION': 'CAST(1 as int)',
                     'VALUE_AS_NUMBER': 'C.value_as_number', 'RANGE_HIGH': 'C.range_high', 'RANGE_LOW': 'C.range_low'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', get_codeset_join_expression(
            c.codeset_id, 'm.measurement_concept_id', c.measurement_source_concept, 'm.measurement_source_concept_id'))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY m.person_id ORDER BY m.measurement_date, '
                                      'm.measurement_id) as ordinal')

    def resolve_select_clauses(self, c):
        s = list(self.DEFAULT_SELECT)
        if _has(c.measurement_type) or c.measurement_type_cs is not None:
            s.append('m.measurement_type_concept_id')
        if _has(c.operator) or c.operator_cs is not None:
            s.append('m.operator_concept_id')
        if _has(c.value_as_concept) or c.value_as_concept_cs is not None:
            s.append('m.value_as_concept_id')
        if _has(c.unit) or c.unit_cs is not None:
            s.append('m.unit_concept_id')
        if _has(c.provider_specialty) or c.provider_specialty_cs is not None:
            s.append('m.provider_id')
        s.append(self._dates(c, 'm.measurement_date', 'DATEADD(day,1,m.measurement_date)',
                             'm.measurement_date as start_date, DATEADD(day,1,m.measurement_date) as end_date'))
        return s

    def resolve_join_clauses(self, c):
        j = [PERSON_JOIN] if _std_person_join(c) else []
        _psv_joins(c, j)
        return j

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.occurrence_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.occurrence_start_date))
        if _has(c.measurement_type):
            w.append(_type_in('C.measurement_type_concept_id', c.measurement_type, c.measurement_type_exclude))
        if c.measurement_type_cs is not None:
            w.append(get_codeset_in_expression('C.measurement_type_concept_id', c.measurement_type_cs))
        if _has(c.operator):
            w.append('C.operator_concept_id in (%s)' % in_concepts(c.operator))
        if c.operator_cs is not None:
            w.append(get_codeset_in_expression('C.operator_concept_id', c.operator_cs))
        if c.value_as_number is not None:
            w.append(build_numeric_range_clause('C.value_as_number', c.value_as_number, '.4f'))
        if _has(c.value_as_concept):
            w.append('C.value_as_concept_id in (%s)' % in_concepts(c.value_as_concept))
        if c.value_as_concept_cs is not None:
            w.append(get_codeset_in_expression('C.value_as_concept_id', c.value_as_concept_cs))
        if _has(c.unit):
            w.append('C.unit_concept_id in (%s)' % in_concepts(c.unit))
        if c.unit_cs is not None:
            w.append(get_codeset_in_expression('C.unit_concept_id', c.unit_cs))
        if c.range_low is not None:
            w.append(build_numeric_range_clause('C.range_low', c.range_low, '.4f'))
        if c.range_high is not None:
            w.append(build_numeric_range_clause('C.range_high', c.range_high, '.4f'))
        if c.range_low_ratio is not None:
            w.append(build_numeric_range_clause('(C.value_as_number / NULLIF(C.range_low, 0))', c.range_low_ratio, '.4f'))
        if c.range_high_ratio is not None:
            w.append(build_numeric_range_clause('(C.value_as_number / NULLIF(C.range_high, 0))', c.range_high_ratio,
                                                '.4f'))
        if c.abnormal is not None and c.abnormal:
            w.append('(C.value_as_number < C.range_low or C.value_as_number > C.range_high or '
                     'C.value_as_concept_id in (4155142, 4155143))')
        _age_gender_where(c, w)
        _ps_visit_where(c, w)
        return w


def _user_period_where(c, w):
    if c.first is not None and c.first:
        w.append('C.ordinal = 1')
    if c.user_defined_period is not None:
        p = c.user_defined_period
        if p.start_date is not None:
            e = date_string_to_sql(p.start_date)
            w.append('C.start_date <= %s and C.end_date >= %s' % (e, e))
        if p.end_date is not None:
            e = date_string_to_sql(p.end_date)
            w.append('C.start_date <= %s and C.end_date >= %s' % (e, e))
    if c.period_start_date is not None:
        w.append(build_date_range_clause('C.start_date', c.period_start_date))
    if c.period_end_date is not None:
        w.append(build_date_range_clause('C.end_date', c.period_end_date))


def _user_period_sql(q, c):
    p = c.user_defined_period
    s = date_string_to_sql(p.start_date) if p is not None and p.start_date is not None else 'C.start_date'
    q = jreplace(q, '@startDateExpression', s)
    e = date_string_to_sql(p.end_date) if p is not None and p.end_date is not None else 'C.end_date'
    return jreplace(q, '@endDateExpression', e)


class ObservationPeriodSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'observationPeriod.sql'
    LABEL = 'Observation Period'
    DEFAULT_COLUMNS = SEV
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.period_type_concept_id',
                     'DURATION': 'DATEDIFF(d, @startDateExpression, @endDateExpression)'}

    def get_criteria_sql(self, c, options=None):
        return _user_period_sql(super().get_criteria_sql(c, options), c)

    def resolve_select_clauses(self, c):
        return ['op.person_id', 'op.observation_period_id', 'op.period_type_concept_id', self._dates(
            c, 'op.observation_period_start_date', 'op.observation_period_end_date',
            'op.observation_period_start_date as start_date, op.observation_period_end_date as end_date')]

    def resolve_join_clauses(self, c):
        return [PERSON_JOIN] if c.age_at_start is not None or c.age_at_end is not None else []

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        _user_period_where(c, w)
        if _has(c.period_type):
            w.append('C.period_type_concept_id in (%s)' % in_concepts(c.period_type))
        if c.period_type_cs is not None:
            w.append(get_codeset_in_expression('C.period_type_concept_id', c.period_type_cs))
        if c.period_length is not None:
            w.append(build_numeric_range_clause('DATEDIFF(d,C.start_date, C.end_date)', c.period_length))
        if c.age_at_start is not None:
            w.append(build_numeric_range_clause('YEAR(C.start_date) - P.year_of_birth', c.age_at_start))
        if c.age_at_end is not None:
            w.append(build_numeric_range_clause('YEAR(C.end_date) - P.year_of_birth', c.age_at_end))
        return w


class ObservationSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'observation.sql'
    LABEL = 'Observation'
    DEFAULT_COLUMNS = SEV
    DEFAULT_SELECT = ['o.person_id', 'o.observation_id', 'o.observation_concept_id', 'o.visit_occurrence_id',
                      'o.value_as_number']
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.observation_concept_id', 'VALUE_AS_NUMBER': 'C.value_as_number',
                     'DURATION': 'CAST(1 as int)'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', get_codeset_join_expression(
            c.codeset_id, 'o.observation_concept_id', c.observation_source_concept, 'o.observation_source_concept_id'))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY o.person_id ORDER BY o.observation_date, '
                                      'o.observation_id) as ordinal')

    def resolve_select_clauses(self, c):
        s = list(self.DEFAULT_SELECT)
        if _has(c.observation_type) or c.observation_type_cs is not None:
            s.append('o.observation_type_concept_id')
        if c.value_as_string is not None:
            s.append('o.value_as_string')
        if _has(c.value_as_concept) or c.value_as_concept_cs is not None:
            s.append('o.value_as_concept_id')
        if _has(c.qualifier) or c.qualifier_cs is not None:
            s.append('o.qualifier_concept_id')
        if _has(c.unit) or c.unit_cs is not None:
            s.append('o.unit_concept_id')
        if _has(c.provider_specialty) or c.provider_specialty_cs is not None:
            s.append('o.provider_id')
        s.append(self._dates(c, 'o.observation_date', 'DATEADD(day,1,o.observation_date)',
                             'o.observation_date as start_date, DATEADD(day,1,o.observation_date) as end_date'))
        return s

    def resolve_join_clauses(self, c):
        j = [PERSON_JOIN] if _std_person_join(c) else []
        _psv_joins(c, j)
        return j

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.occurrence_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.occurrence_start_date))
        if _has(c.observation_type):
            w.append(_type_in('C.observation_type_concept_id', c.observation_type, c.observation_type_exclude))
        if c.observation_type_cs is not None:
            w.append(get_codeset_in_expression('C.observation_type_concept_id', c.observation_type_cs))
        if c.value_as_number is not None:
            w.append(build_numeric_range_clause('C.value_as_number', c.value_as_number, '.4f'))
        if c.value_as_string is not None:
            w.append(build_text_filter_clause('C.value_as_string', c.value_as_string))
        if _has(c.value_as_concept):
            w.append('C.value_as_concept_id in (%s)' % in_concepts(c.value_as_concept))
        if c.value_as_concept_cs is not None:
            w.append(get_codeset_in_expression('C.value_as_concept_id', c.value_as_concept_cs))
        if _has(c.qualifier):
            w.append('C.qualifier_concept_id in (%s)' % in_concepts(c.qualifier))
        if c.qualifier_cs is not None:
            w.append(get_codeset_in_expression('C.qualifier_concept_id', c.qualifier_cs))
        if _has(c.unit):
            w.append('C.unit_concept_id in (%s)' % in_concepts(c.unit))
        if c.unit_cs is not None:
            w.append(get_codeset_in_expression('C.unit_concept_id', c.unit_cs))
        _age_gender_where(c, w)
        _ps_visit_where(c, w)
        return w


_PPP = [('payer_concept', 'payer_concept_id'), ('plan_concept', 'plan_concept_id'),
        ('sponsor_concept', 'sponsor_concept_id'), ('stop_reason_concept', 'stop_reason_concept_id'),
        ('payer_source_concept', 'payer_source_concept_id'), ('plan_source_concept', 'plan_source_concept_id'),
        ('sponsor_source_concept', 'sponsor_source_concept_id'),
        ('stop_reason_source_concept', 'stop_reason_source_concept_id')]


class PayerPlanPeriodSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'payerPlanPeriod.sql'
    LABEL = 'Payer Plan Period'
    DEFAULT_COLUMNS = SEV
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.payer_concept_id'}

    def get_criteria_sql(self, c, options=None):
        return _user_period_sql(super().get_criteria_sql(c, options), c)

    def resolve_select_clauses(self, c):
        s = ['ppp.person_id', 'ppp.payer_plan_period_id']
        for attr, col in _PPP:
            if getattr(c, attr) is not None:
                s.append('ppp.' + col)
        s.append(self._dates(c, 'ppp.payer_plan_period_start_date', 'ppp.payer_plan_period_end_date',
                             'ppp.payer_plan_period_start_date as start_date, '
                             'ppp.payer_plan_period_end_date as end_date'))
        return s

    def resolve_join_clauses(self, c):
        return [PERSON_JOIN] if _std_person_join(c, ('age_at_start', 'age_at_end')) else []

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        _user_period_where(c, w)
        if c.period_length is not None:
            w.append(build_numeric_range_clause('DATEDIFF(d,C.start_date, C.end_date)', c.period_length))
        _era_age_gender_where(c, w)
        for attr, col in _PPP:
            v = getattr(c, attr)
            if v is not None:
                w.append('C.%s in (SELECT concept_id from #Codesets where codeset_id = %s)' % (col, jstr(v)))
        return w


class ProcedureOccurrenceSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'procedureOccurrence.sql'
    LABEL = 'Procedure Occurrence'
    DEFAULT_COLUMNS = SEV
    DEFAULT_SELECT = ['po.person_id', 'po.procedure_occurrence_id', 'po.procedure_concept_id',
                      'po.visit_occurrence_id', 'po.quantity']
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.procedure_concept_id', 'DURATION': 'CAST(1 as int)',
                     'QUANTITY': 'C.quantity'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', get_codeset_join_expression(
            c.codeset_id, 'po.procedure_concept_id', c.procedure_source_concept, 'po.procedure_source_concept_id'))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY po.person_id ORDER BY po.procedure_date, '
                                      'po.procedure_occurrence_id) as ordinal')

    def resolve_select_clauses(self, c):
        s = list(self.DEFAULT_SELECT)
        if _has(c.procedure_type) or c.procedure_type_cs is not None:
            s.append('po.procedure_type_concept_id')
        if _has(c.modifier) or c.modifier_cs is not None:
            s.append('po.modifier_concept_id')
        if _has(c.provider_specialty) or c.provider_specialty_cs is not None:
            s.append('po.provider_id')
        s.append(self._dates(c, 'po.procedure_date', 'DATEADD(day,1,po.procedure_date)',
                             'po.procedure_date as start_date, DATEADD(day,1,po.procedure_date) as end_date'))
        return s

    def resolve_join_clauses(self, c):
        j = [PERSON_JOIN] if _std_person_join(c) else []
        _psv_joins(c, j)
        return j

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.occurrence_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.occurrence_start_date))
        if _has(c.procedure_type):
            w.append(_type_in('C.procedure_type_concept_id', c.procedure_type, c.procedure_type_exclude))
        if c.procedure_type_cs is not None:
            w.append(get_codeset_in_expression('C.procedure_type_concept_id', c.procedure_type_cs))
        if _has(c.modifier):
            w.append('C.modifier_concept_id in (%s)' % in_concepts(c.modifier))
        if c.modifier_cs is not None:
            w.append(get_codeset_in_expression('C.modifier_concept_id', c.modifier_cs))
        if c.quantity is not None:
            w.append(build_numeric_range_clause('C.quantity', c.quantity))
        _age_gender_where(c, w)
        _ps_visit_where(c, w)
        return w


class SpecimenSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'specimen.sql'
    LABEL = 'Specimen'
    DEFAULT_COLUMNS = SEV
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.specimen_concept_id', 'DURATION': 'CAST(1 as int)'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', _codeset_where('s.specimen_concept_id', c.codeset_id))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY s.person_id ORDER BY s.specimen_date, '
                                      's.specimen_id) as ordinal')

    def resolve_join_clauses(self, c):
        return [PERSON_JOIN] if _std_person_join(c) else []

    def resolve_where_clauses(self, c):
        w = []          # the Java original does not call super either
        if c.occurrence_start_date is not None:
            w.append(build_date_range_clause('C.specimen_date', c.occurrence_start_date))
        if _has(c.specimen_type):
            w.append(_type_in('C.specimen_type_concept_id', c.specimen_type, c.specimen_type_exclude))
        if c.specimen_type_cs is not None:
            w.append(get_codeset_in_expression('C.specimen_type_concept_id', c.specimen_type_cs))
        if c.quantity is not None:
            w.append(build_numeric_range_clause('C.quantity', c.quantity, '.4f'))
        if _has(c.unit):
            w.append('C.unit_concept_id in (%s)' % in_concepts(c.unit))
        if c.unit_cs is not None:
            w.append(get_codeset_in_expression('C.unit_concept_id', c.unit_cs))
        if _has(c.anatomic_site):
            w.append('C.anatomic_site_concept_id in (%s)' % in_concepts(c.anatomic_site))
        if c.anatomic_site_cs is not None:
            w.append(get_codeset_in_expression('C.anatomic_site_concept_id', c.anatomic_site_cs))
        if _has(c.disease_status):
            w.append('C.disease_status_concept_id in (%s)' % in_concepts(c.disease_status))
        if c.disease_status_cs is not None:
            w.append(get_codeset_in_expression('C.disease_status_concept_id', c.disease_status_cs))
        if c.source_id is not None:
            w.append(build_text_filter_clause('C.specimen_source_id', c.source_id))
        _age_gender_where(c, w, 'YEAR(C.specimen_date) - P.year_of_birth')
        return w


def _location_history_join(start_col, end_col):
    return ('JOIN @cdm_database_schema.LOCATION_HISTORY LH on LH.entity_id = C.care_site_id '
            "AND LH.domain_id = 'CARE_SITE' AND %s >= LH.start_date "
            'AND %s <= ISNULL(LH.end_date, DATEFROMPARTS(2099,12,31))' % (start_col, end_col))


def _care_site_region(joins, codeset_id, start_col, end_col):
    joins.append(_location_history_join(start_col, end_col))
    joins.append('JOIN @cdm_database_schema.LOCATION LOC on LOC.location_id = LH.location_id')
    joins.append(get_codeset_join_expression(codeset_id, 'LOC.region_concept_id', None, None))


CARE_SITE_JOIN = 'JOIN @cdm_database_schema.CARE_SITE CS on C.care_site_id = CS.care_site_id'


class VisitDetailSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'visitDetail.sql'
    LABEL = 'Visit Detail'
    DEFAULT_COLUMNS = {'START_DATE', 'END_DATE', 'VISIT_DETAIL_ID'}
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.visit_detail_concept_id',
                     'DURATION': 'DATEDIFF(d, C.start_date, C.end_date)'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', get_codeset_join_expression(
            c.codeset_id, 'vd.visit_detail_concept_id', c.visit_detail_source_concept,
            'vd.visit_detail_source_concept_id'))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY vd.person_id ORDER BY '
                                      'vd.visit_detail_start_date, vd.visit_detail_id) as ordinal')

    def resolve_select_clauses(self, c):
        s = ['vd.person_id', 'vd.visit_detail_id', 'vd.visit_detail_concept_id', 'vd.visit_occurrence_id']
        if c.visit_detail_type_cs is not None:
            s.append('vd.visit_detail_type_concept_id')
        if c.provider_specialty_cs is not None:
            s.append('vd.provider_id')
        if c.place_of_service_cs is not None:
            s.append('vd.care_site_id')
        s.append(self._dates(c, 'vd.visit_detail_start_date', 'vd.visit_detail_end_date',
                             'vd.visit_detail_start_date as start_date, vd.visit_detail_end_date as end_date'))
        return s

    def resolve_join_clauses(self, c):
        j = []
        if c.age is not None or c.gender_cs is not None:
            j.append(PERSON_JOIN)
        if c.place_of_service_cs is not None or c.place_of_service_location is not None:
            j.append(CARE_SITE_JOIN)
        if c.provider_specialty_cs is not None:
            j.append(PROVIDER_JOIN)
        if c.place_of_service_location is not None:
            _care_site_region(j, c.place_of_service_location, 'C.visit_detail_start_date', 'C.visit_detail_end_date')
        return j

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.visit_detail_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.visit_detail_start_date))
        if c.visit_detail_end_date is not None:
            w.append(build_date_range_clause('C.end_date', c.visit_detail_end_date))
        if c.visit_detail_type_cs is not None:
            w.append(get_codeset_in_expression('C.visit_detail_type_concept_id', c.visit_detail_type_cs))
        if c.visit_detail_length is not None:
            w.append(build_numeric_range_clause('DATEDIFF(d,C.start_date, C.end_date)', c.visit_detail_length))
        if c.age is not None:
            w.append(build_numeric_range_clause('YEAR(C.end_date) - P.year_of_birth', c.age))
        if c.gender_cs is not None:
            w.append(get_codeset_in_expression('P.gender_concept_id', c.gender_cs))
        if c.provider_specialty_cs is not None:
            w.append(get_codeset_in_expression('PR.specialty_concept_id', c.provider_specialty_cs))
        if c.place_of_service_cs is not None:
            w.append(get_codeset_in_expression('CS.place_of_service_concept_id', c.place_of_service_cs))
        return w


class VisitOccurrenceSqlBuilder(CriteriaSqlBuilder):
    TEMPLATE = 'visitOccurrence.sql'
    LABEL = 'Visit Occurrence'
    DEFAULT_COLUMNS = SEV
    TABLE_COLUMNS = {'DOMAIN_CONCEPT': 'C.visit_concept_id', 'DURATION': 'DATEDIFF(d, C.start_date, C.end_date)'}

    def embed_codeset_clause(self, q, c):
        return jreplace(q, '@codesetClause', get_codeset_join_expression(
            c.codeset_id, 'vo.visit_concept_id', c.visit_source_concept, 'vo.visit_source_concept_id'))

    def embed_ordinal_expression(self, q, c, w):
        return self._ordinal(q, c, w, ', row_number() over (PARTITION BY vo.person_id ORDER BY vo.visit_start_date, '
                                      'vo.visit_occurrence_id) as ordinal')

    def resolve_select_clauses(self, c):
        s = ['vo.person_id', 'vo.visit_occurrence_id', 'vo.visit_concept_id']
        if _has(c.visit_type) or c.visit_type_cs is not None:
            s.append('vo.visit_type_concept_id')
        if _has(c.provider_specialty) or c.provider_specialty_cs is not None:
            s.append('vo.provider_id')
        if _has(c.place_of_service) or c.place_of_service_cs is not None:
            s.append('vo.care_site_id')
        s.append(self._dates(c, 'vo.visit_start_date', 'vo.visit_end_date',
                             'vo.visit_start_date as start_date, vo.visit_end_date as end_date'))
        return s

    def resolve_join_clauses(self, c):
        j = [PERSON_JOIN] if _std_person_join(c) else []
        if _has(c.place_of_service) or c.place_of_service_cs is not None or c.place_of_service_location is not None:
            j.append(CARE_SITE_JOIN)
        if _has(c.provider_specialty) or c.provider_specialty_cs is not None:
            j.append(PROVIDER_JOIN)
        if c.place_of_service_location is not None:
            _care_site_region(j, c.place_of_service_location, 'C.visit_start_date', 'C.visit_end_date')
        return j

    def resolve_where_clauses(self, c):
        w = super().resolve_where_clauses(c)
        if c.occurrence_start_date is not None:
            w.append(build_date_range_clause('C.start_date', c.occurrence_start_date))
        if c.occurrence_end_date is not None:
            w.append(build_date_range_clause('C.end_date', c.occurrence_end_date))
        if _has(c.visit_type):
            w.append(_type_in('C.visit_type_concept_id', c.visit_type, c.visit_type_exclude))
        if c.visit_type_cs is not None:
            w.append(get_codeset_in_expression('C.visit_type_concept_id', c.visit_type_cs))
        if c.visit_length is not None:
            w.append(build_numeric_range_clause('DATEDIFF(d,C.start_date, C.end_date)', c.visit_length))
        if c.age is not None:
            w.append(build_numeric_range_clause('YEAR(C.start_date) - P.year_of_birth', c.age))
        if _has(c.gender):
            w.append('P.gender_concept_id in (%s)' % in_concepts(c.gender))
        if c.gender_cs is not None:
            w.append(get_codeset_in_expression('P.gender_concept_id', c.gender_cs))
        if _has(c.provider_specialty):
            w.append('PR.specialty_concept_id in (%s)' % in_concepts(c.provider_specialty))
        if c.provider_specialty_cs is not None:
            w.append(get_codeset_in_expression('PR.specialty_concept_id', c.provider_specialty_cs))
        if _has(c.place_of_service):
            w.append('CS.place_of_service_concept_id in (%s)' % in_concepts(c.place_of_service))
        if c.place_of_service_cs is not None:
            w.append(get_codeset_in_expression('CS.place_of_service_concept_id', c.place_of_service_cs))
        return w


BUILDERS = {
    'ConditionEra': ConditionEraSqlBuilder(), 'ConditionOccurrence': ConditionOccurrenceSqlBuilder(),
    'Death': DeathSqlBuilder(), 'DeviceExposure': DeviceExposureSqlBuilder(), 'DoseEra': DoseEraSqlBuilder(),
    'DrugEra': DrugEraSqlBuilder(), 'DrugExposure': DrugExposureSqlBuilder(), 'CustomEra': CustomEraSqlBuilder(),
    'Episode': EpisodeSqlBuilder(), 'LocationRegion': LocationRegionSqlBuilder(),
    'Measurement': MeasurementSqlBuilder(), 'Observation': ObservationSqlBuilder(),
    'ObservationPeriod': ObservationPeriodSqlBuilder(), 'PayerPlanPeriod': PayerPlanPeriodSqlBuilder(),
    'ProcedureOccurrence': ProcedureOccurrenceSqlBuilder(), 'Specimen': SpecimenSqlBuilder(),
    'VisitDetail': VisitDetailSqlBuilder(), 'VisitOccurrence': VisitOccurrenceSqlBuilder(),
}
