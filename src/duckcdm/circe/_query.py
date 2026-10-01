"""CohortExpressionQueryBuilder · ConceptSetExpressionQueryBuilder port."""
import json

from ._builders import (BUILDERS, build_date_range_clause, build_numeric_range_clause, concept_ids,
                        date_string_to_sql, get_codeset_in_expression, in_concepts, split_in_clause, template)
from ._jutil import CirceError, i32, is_empty, jjoin, jreplace, jstr, nn, regex_replace_all
from ._model import (COLUMN_NAME, CohortExpression, ConceptSetExpression, CustomEraStrategy, DateOffsetStrategy,
                     Occurrence, to_bool, to_integer, to_str)

MAX_IN_LENGTH = 1000
DEFAULT_COHORT_ID_FIELD_NAME = 'cohort_definition_id'
DEFAULT_DRUG_EXPOSURE_END_DATE_EXPRESSION = ('COALESCE(DRUG_EXPOSURE_END_DATE, DATEADD(day,DAYS_SUPPLY,'
                                             'DRUG_EXPOSURE_START_DATE), DATEADD(day,1,DRUG_EXPOSURE_START_DATE))')


def eq_ic(a, b):
    """String.equalsIgnoreCase (NPE if a is null)."""
    nn(a, 'string')
    if b is None or len(a) != len(b):
        return False
    return all(x == y or x.upper() == y.upper() or x.lower() == y.lower() for x, y in zip(a, b))


# ---- concept sets ------------------------------------------------------------

def _concept_set_sub_query(concepts, descendants):
    queries = []
    if concepts:
        queries.append(jreplace(template('conceptSetQuery.sql'), '@conceptIdIn',
                                split_in_clause('concept_id', concept_ids(concepts), MAX_IN_LENGTH)))
    if descendants:
        queries.append(jreplace(template('conceptSetDescendants.sql'), '@conceptIdIn',
                                split_in_clause('ca.ancestor_concept_id', concept_ids(descendants), MAX_IN_LENGTH)))
    return 'UNION'.join(queries)


def _concept_set_query(concepts, descendants, mapped, mapped_desc):
    if not concepts:
        return 'select concept_id from @vocabulary_database_schema.CONCEPT where 0=1'
    q = _concept_set_sub_query(concepts, descendants)
    if mapped or mapped_desc:
        q += 'UNION\n' + jreplace(template('conceptSetMapped.sql'), '@conceptsetQuery',
                                  _concept_set_sub_query(mapped, mapped_desc))
    return q


def build_concept_set_query(expression):
    """ConceptSetExpressionQueryBuilder.buildExpressionQuery."""
    if not isinstance(expression, ConceptSetExpression):
        expression = ConceptSetExpression.parse(json.loads(expression) if isinstance(expression, str) else expression)
    inc, inc_d, inc_m, inc_md, exc, exc_d, exc_m, exc_md = ([] for _ in range(8))
    for item in nn(nn(expression, 'expression').items, 'items'):
        nn(item, 'item')
        if not item.is_excluded:
            a, d, m, md = inc, inc_d, inc_m, inc_md
        else:
            a, d, m, md = exc, exc_d, exc_m, exc_md
        a.append(item.concept)
        if item.include_descendants:
            d.append(item.concept)
        if item.include_mapped:
            m.append(item.concept)
            if item.include_descendants:
                md.append(item.concept)
    q = jreplace(template('conceptSetInclude.sql'), '@includeQuery', _concept_set_query(inc, inc_d, inc_m, inc_md))
    if exc:
        q += jreplace(template('conceptSetExclude.sql'), '@excludeQuery', _concept_set_query(exc, exc_d, exc_m, exc_md))
    return q


# ---- cohorts -----------------------------------------------------------------

class BuilderOptions:
    def __init__(self):
        self.additional_columns = []


class BuildExpressionQueryOptions:
    """cohortIdFieldName, cohortId, cdmSchema, targetTable, resultSchema, vocabularySchema, generateStats."""

    def __init__(self, cohort_id_field_name=None, cohort_id=None, cdm_schema=None, target_table=None,
                 result_schema=None, vocabulary_schema=None, generate_stats=False):
        self.cohort_id_field_name = cohort_id_field_name
        self.cohort_id = cohort_id
        self.cdm_schema = cdm_schema
        self.target_table = target_table
        self.result_schema = result_schema
        self.vocabulary_schema = vocabulary_schema
        self.generate_stats = generate_stats

    @classmethod
    def from_json(cls, text):
        v = json.loads(text) if isinstance(text, str) else text
        if v is None:
            return None
        if not isinstance(v, dict):
            raise CirceError('RuntimeException: Error parsing expression query options')
        try:
            return cls(to_str(v.get('cohortIdFieldName')), to_integer(v.get('cohortId')), to_str(v.get('cdmSchema')),
                       to_str(v.get('targetTable')), to_str(v.get('resultSchema')),
                       to_str(v.get('vocabularySchema')), to_bool(v.get('generateStats')))
        except CirceError:
            raise CirceError('RuntimeException: Error parsing expression query options')


def _occurrence_operator(t):
    if t == 0:
        return '='
    if t == 1:
        return '<='
    if t == 2:
        return '>='
    raise CirceError('RuntimeException: Invalid occurrene operator recieved: type=%d.' % t)


class CohortExpressionQueryBuilder:

    def __init__(self):
        self.additional_criteria_inner = jreplace(template('additionalCriteriaInclude.sql'), '@windowedCriteria',
                                                  template('windowedCriteria.sql'))
        self.additional_criteria_left = jreplace(template('additionalCriteriaExclude.sql'), '@windowedCriteria',
                                                 template('windowedCriteria.sql'))

    # -- criteria dispatch
    def criteria_sql(self, c, options=None):
        nn(c, 'criteria')
        if c.TYPE == 'CustomEra':
            inner = self._custom_era_criteria_query(c, options)
            return BUILDERS['CustomEra'].get_criteria_sql_with(c, options, inner)
        query = BUILDERS[c.TYPE].get_criteria_sql(c, options)
        if c.correlated_criteria is not None and not c.correlated_criteria.is_empty():
            query = self._wrap_criteria_query(query, c.correlated_criteria)
        return query

    def _custom_era_criteria_query(self, c, options):
        if c.criteria_list is None or len(c.criteria_list) == 0:
            raise CirceError('RuntimeException: CustomEra.CriteriaList can not be null or empty.')
        return '\nUNION ALL\n'.join('select person_id, start_date, end_date from (%s) C' % self.criteria_sql(x, options)
                                    for x in c.criteria_list)

    def _wrap_criteria_query(self, query, group):
        event_query = jreplace(template('eventTableExpression.sql'), '@eventQuery', query)
        group_query = self.get_criteria_group_query(group, '(%s)' % event_query)
        group_query = jreplace(group_query, '@indexId', '0')
        return ('select PE.person_id, PE.event_id, PE.start_date, PE.end_date, PE.visit_occurrence_id, PE.sort_date '
                'FROM (\n%s\n) PE\nJOIN (\n%s) AC on AC.person_id = pe.person_id and AC.event_id = pe.event_id\n'
                % (query, group_query))

    # -- fragments
    def get_codeset_query(self, concept_sets):
        if concept_sets is None or len(concept_sets) <= 0:
            return jreplace(template('codesetQuery.sql'), '@codesetInserts', '')
        union = ' UNION ALL \n'.join(
            'SELECT %d as codeset_id, c.concept_id FROM (%s) C' % (nn(cs, 'conceptSet').id,
                                                                   build_concept_set_query(cs.expression))
            for cs in concept_sets)
        return jreplace(template('codesetQuery.sql'), '@codesetInserts',
                        'INSERT INTO #Codesets (codeset_id, concept_id)\n' + union + ';')

    def _censoring_events_query(self, censoring):
        return '\nUNION ALL\n'.join(jreplace(template('censoringInsert.sql'), '@criteriaQuery', self.criteria_sql(c))
                                    for c in censoring)

    def get_primary_events_query(self, pc):
        nn(pc, 'primaryCriteria')
        query = template('primaryEventsQuery.sql')
        queries = [self.criteria_sql(c) for c in nn(pc.criteria_list, 'criteriaList')]
        query = jreplace(query, '@criteriaQueries', '\nUNION ALL\n'.join(queries))
        ow = nn(pc.observation_window, 'observationWindow')
        filt = ('DATEADD(day,%d,OP.OBSERVATION_PERIOD_START_DATE) <= E.START_DATE AND DATEADD(day,%d,E.START_DATE) <= '
                'OP.OBSERVATION_PERIOD_END_DATE' % (ow.prior_days, ow.post_days))
        query = jreplace(query, '@primaryEventsFilter', filt)
        lim = nn(pc.primary_limit, 'primaryLimit')
        query = jreplace(query, '@EventSort', 'DESC' if lim.type is not None and eq_ic(lim.type, 'LAST') else 'ASC')
        query = jreplace(query, '@primaryEventLimit', '' if eq_ic(lim.type, 'ALL') else 'WHERE P.ordinal = 1')
        return query

    def get_final_cohort_query(self, censor_window):
        query = 'select @target_cohort_id as @cohort_id_field_name, person_id, @start_date, @end_date \nFROM #final_cohort CO'
        start, end = 'start_date', 'end_date'
        if censor_window is not None and (censor_window.start_date is not None or censor_window.end_date is not None):
            if censor_window.start_date is not None:
                s = date_string_to_sql(censor_window.start_date)
                start = 'CASE WHEN start_date > ' + s + ' THEN start_date ELSE ' + s + ' END'
            if censor_window.end_date is not None:
                e = date_string_to_sql(censor_window.end_date)
                end = 'CASE WHEN end_date < ' + e + ' THEN end_date ELSE ' + e + ' END'
            query += '\nWHERE @start_date <= @end_date'
        query = jreplace(query, '@start_date', start)
        return jreplace(query, '@end_date', end)

    def _inclusion_rule_table_sql(self, expression):
        if len(expression.inclusion_rules) == 0:
            return 'CREATE TABLE #inclusion_rules (rule_sequence int);'
        unions = ' UNION ALL '.join('SELECT CAST(%d as int) as rule_sequence' % i
                                    for i in range(len(expression.inclusion_rules)))
        return jreplace(template('inclusionRuleTempTable.sql'), '@inclusionRuleUnions', unions)

    def _inclusion_analysis_query(self, event_table, mode):
        q = jreplace(template('cohortInclusionAnalysis.sql'), '@inclusionImpactMode', str(mode))
        return jreplace(q, '@eventTable', event_table)

    def _inclusion_rule_query(self, group):
        q = template('inclusionrule.sql')
        ac = ('\nJOIN (\n' + self.get_criteria_group_query(group, '#qualified_events')
              + ') AC on AC.person_id = pe.person_id AND AC.event_id = pe.event_id')
        ac = jreplace(ac, '@indexId', '0')
        return jreplace(q, '@additionalCriteriaQuery', ac)

    def get_criteria_group_query(self, group, event_table):
        nn(group, 'group')
        query = template('groupQuery.sql')
        queries = []
        join_type = 'INNER'
        index_id = 0
        for cc in nn(group.criteria_list, 'criteriaList'):
            queries.append(jreplace(self.get_corelated_criteria_query(cc, event_table), '@indexId', str(index_id)))
            index_id += 1
        for dc in nn(group.demographic_criteria_list, 'demographicCriteriaList'):
            queries.append(jreplace(self.get_demographic_criteria_query(dc, event_table), '@indexId', str(index_id)))
            index_id += 1
        for g in nn(group.groups, 'groups'):
            queries.append(jreplace(self.get_criteria_group_query(g, event_table), '@indexId', str(index_id)))
            index_id += 1
        if not group.is_empty():
            query = jreplace(query, '@criteriaQueries', '\nUNION ALL\n'.join(queries))
            clause = 'HAVING COUNT(index_id) '
            if eq_ic(group.type, 'ALL'):
                clause += '= ' + str(index_id)
            if eq_ic(group.type, 'ANY'):
                clause += '> 0'
            if group.type.upper().startswith('AT_'):
                if group.type.upper().endswith('LEAST'):
                    clause += '>= ' + jstr(group.count)
                else:
                    clause += '<= ' + jstr(group.count)
                    join_type = 'LEFT'
                if nn(group.count, 'count (unboxing)') == 0:
                    join_type = 'LEFT'
            query = jreplace(query, '@occurrenceCountClause', clause)
            query = jreplace(query, '@joinType', join_type)
        else:
            query = ('-- Begin Criteria Group\n select @indexId as index_id, person_id, event_id FROM @eventTable\n'
                     '-- End Criteria Group\n')
        return jreplace(query, '@eventTable', event_table)

    def get_demographic_criteria_query(self, c, event_table):
        nn(c, 'demographicCriteria')
        query = jreplace(template('demographicCriteria.sql'), '@eventTable', event_table)
        w = []
        if c.age is not None:
            w.append(build_numeric_range_clause('YEAR(E.start_date) - P.year_of_birth', c.age))
        if c.gender is not None and len(c.gender) > 0:
            w.append('P.gender_concept_id in (%s)' % in_concepts(c.gender))
        if c.gender_cs is not None:
            w.append(get_codeset_in_expression('P.gender_concept_id', c.gender_cs))
        if c.race is not None and len(c.race) > 0:
            w.append('P.race_concept_id in (%s)' % in_concepts(c.race))
        if c.race_cs is not None:
            w.append(get_codeset_in_expression('P.race_concept_id', c.race_cs))
        if c.ethnicity is not None and len(c.ethnicity) > 0:
            w.append('P.ethnicity_concept_id in (%s)' % in_concepts(c.ethnicity))
        if c.ethnicity_cs is not None:
            w.append(get_codeset_in_expression('P.ethnicity_concept_id', c.ethnicity_cs))
        if c.occurrence_start_date is not None:
            w.append(build_date_range_clause('E.start_date', c.occurrence_start_date))
        if c.occurrence_end_date is not None:
            w.append(build_date_range_clause('E.end_date', c.occurrence_end_date))
        return jreplace(query, '@whereClause', ('WHERE ' + ' AND '.join(w)) if w else '')

    def get_windowed_criteria_query(self, sql_template, criteria, event_table, options):
        query = sql_template
        check_op = not criteria.ignore_observation_period
        criteria_query = self.criteria_sql(nn(criteria.criteria, 'criteria'), options)
        query = jreplace(query, '@criteriaQuery', criteria_query)
        query = jreplace(query, '@eventTable', event_table)
        if options is not None and len(options.additional_columns) > 0:
            query = jreplace(query, '@additionalColumns',
                             ', ' + ','.join('A.' + COLUMN_NAME[c] for c in options.additional_columns))
        else:
            query = jreplace(query, '@additionalColumns', '')

        clauses = []
        if check_op:
            clauses.append('A.START_DATE >= P.OP_START_DATE AND A.START_DATE <= P.OP_END_DATE')

        def point(ep, index_expr):
            nn(ep, 'endpoint')
            if ep.days is not None:
                return 'DATEADD(day,%d,%s)' % (i32(ep.coeff * ep.days), index_expr)
            if check_op:
                return 'P.OP_START_DATE' if ep.coeff == -1 else 'P.OP_END_DATE'
            return None

        sw = nn(criteria.start_window, 'startWindow')
        s_index = 'P.END_DATE' if sw.use_index_end is not None and sw.use_index_end else 'P.START_DATE'
        s_event = 'A.END_DATE' if sw.use_event_end is not None and sw.use_event_end else 'A.START_DATE'
        e = point(sw.start, s_index)
        if e is not None:
            clauses.append('%s >= %s' % (s_event, e))
        e = point(sw.end, s_index)
        if e is not None:
            clauses.append('%s <= %s' % (s_event, e))

        ew = criteria.end_window
        if ew is not None:
            e_index = 'P.END_DATE' if ew.use_index_end is not None and ew.use_index_end else 'P.START_DATE'
            e_event = 'A.END_DATE' if ew.use_event_end is None or ew.use_event_end else 'A.START_DATE'
            e = point(ew.start, e_index)
            if e is not None:
                clauses.append('%s >= %s' % (e_event, e))
            e = point(ew.end, e_index)
            if e is not None:
                clauses.append('%s <= %s' % (e_event, e))

        if criteria.restrict_visit:
            clauses.append('A.visit_occurrence_id = P.visit_occurrence_id')
        return jreplace(query, '@windowCriteria', (' AND ' + ' AND '.join(clauses)) if clauses else '')

    def get_corelated_criteria_query(self, cc, event_table):
        nn(cc, 'corelatedCriteria')
        occ = nn(cc.occurrence, 'occurrence')
        query = (self.additional_criteria_left if occ.type == Occurrence.AT_MOST or occ.count == 0
                 else self.additional_criteria_inner)
        count_expr = 'cc.event_id'
        opts = BuilderOptions()
        if occ.is_distinct:
            col = 'DOMAIN_CONCEPT' if occ.count_column is None else occ.count_column
            opts.additional_columns.append(col)
            count_expr = 'cc.%s' % COLUMN_NAME[col]
        query = self.get_windowed_criteria_query(query, cc, event_table, opts)
        crit = 'HAVING COUNT(%s%s) %s %d' % ('DISTINCT ' if occ.is_distinct else '', count_expr,
                                             _occurrence_operator(occ.type), occ.count)
        return jreplace(query, '@occurrenceCriteria', crit)

    # -- end strategy
    def strategy_sql(self, strat, event_table):
        if isinstance(strat, DateOffsetStrategy):
            s = jreplace(template('dateOffsetStrategy.sql'), '@eventTable', event_table)
            s = jreplace(s, '@offset', str(strat.offset))
            if strat.date_field is None:
                raise CirceError('NullPointerException: switch on null')
            return jreplace(s, '@dateField', 'end_date' if strat.date_field == 'EndDate' else 'start_date')
        if strat.drug_codeset_id is None:
            raise CirceError('RuntimeException: Drug Codeset ID can not be NULL.')
        end_expr = DEFAULT_DRUG_EXPOSURE_END_DATE_EXPRESSION
        if strat.days_supply_override is not None:
            end_expr = 'DATEADD(day,%d,DRUG_EXPOSURE_START_DATE)' % strat.days_supply_override
        s = jreplace(template('customEraStrategy.sql'), '@eventTable', event_table)
        s = jreplace(s, '@drugCodesetId', str(strat.drug_codeset_id))
        s = jreplace(s, '@gapDays', str(strat.gap_days))
        s = jreplace(s, '@offset', str(strat.offset))
        return jreplace(s, '@drugExposureEndDateExpression', end_expr)

    # -- whole query
    def build_expression_query(self, expression, options=None):
        if not isinstance(expression, CohortExpression):
            expression = CohortExpression.from_json(expression)
        nn(expression, 'expression')
        sql = template('generateCohort.sql')
        sql = jreplace(sql, '@codesetQuery', self.get_codeset_query(expression.concept_sets))
        primary = self.get_primary_events_query(expression.primary_criteria)
        sql = jreplace(sql, '@primaryEventsQuery', primary)

        ac_query = ''
        ac = expression.additional_criteria
        if ac is not None and not ac.is_empty():
            g = jreplace(self.get_criteria_group_query(ac, '(%s)' % primary), '@indexId', '0')
            ac_query = '\nJOIN (\n' + g + ') AC on AC.person_id = pe.person_id and AC.event_id = pe.event_id\n'
        sql = jreplace(sql, '@additionalCriteriaQuery', ac_query)

        ql = nn(expression.qualified_limit, 'qualifiedLimit')
        sql = jreplace(sql, '@QualifiedEventSort', 'DESC' if ql.type is not None and eq_ic(ql.type, 'LAST') else 'ASC')
        if ac is not None and ql.type is not None and not eq_ic(ql.type, 'ALL'):
            sql = jreplace(sql, '@QualifiedLimitFilter', 'WHERE QE.ordinal = 1')
        else:
            sql = jreplace(sql, '@QualifiedLimitFilter', '')

        rules = nn(expression.inclusion_rules, 'inclusionRules')
        if len(rules) > 0:
            inserts, temps = [], []
            for i, rule in enumerate(rules):
                ins = self._inclusion_rule_query(nn(rule, 'inclusionRule').expression)
                inserts.append(jreplace(ins, '@inclusion_rule_id', str(i)))
                temps.append('#Inclusion_%d' % i)
            union = '\nUNION ALL\n'.join('select inclusion_rule_id, person_id, event_id from %s' % t for t in temps)
            inserts.append('SELECT inclusion_rule_id, person_id, event_id\nINTO #inclusion_events\nFROM (%s) I;' % union)
            inserts.extend('TRUNCATE TABLE %s;\nDROP TABLE %s;\n' % (t, t) for t in temps)
            sql = jreplace(sql, '@inclusionCohortInserts', '\n'.join(inserts))
        else:
            sql = jreplace(sql, '@inclusionCohortInserts',
                           'create table #inclusion_events (inclusion_rule_id bigint,\n\tperson_id bigint,\n\t'
                           'event_id bigint\n);')

        el = nn(expression.expression_limit, 'expressionLimit')
        sql = jreplace(sql, '@IncludedEventSort', 'DESC' if el.type is not None and eq_ic(el.type, 'LAST') else 'ASC')
        if el.type is not None and not eq_ic(el.type, 'ALL'):
            sql = jreplace(sql, '@ResultLimitFilter', 'WHERE Results.ordinal = 1')
        else:
            sql = jreplace(sql, '@ResultLimitFilter', '')
        sql = jreplace(sql, '@ruleTotal', str(len(rules)))

        ends = []
        strat = expression.end_strategy
        if not isinstance(strat, DateOffsetStrategy):
            ends.append("-- By default, cohort exit at the event's op end date\n"
                        'select event_id, person_id, op_end_date as end_date from #included_events')
        if strat is not None:
            sql = jreplace(sql, '@strategy_ends_temp_tables', self.strategy_sql(strat, '#included_events'))
            sql = jreplace(sql, '@strategy_ends_cleanup', 'TRUNCATE TABLE #strategy_ends;\nDROP TABLE #strategy_ends;\n')
            ends.append('-- End Date Strategy\n%s\n' % 'SELECT event_id, person_id, end_date from #strategy_ends')
        else:
            sql = jreplace(sql, '@strategy_ends_temp_tables', '')
            sql = jreplace(sql, '@strategy_ends_cleanup', '')
        if expression.censoring_criteria is not None and len(expression.censoring_criteria) > 0:
            ends.append('-- Censor Events\n%s\n' % self._censoring_events_query(expression.censoring_criteria))

        sql = jreplace(sql, '@finalCohortQuery', self.get_final_cohort_query(expression.censor_window))
        sql = jreplace(sql, '@cohort_end_unions', '\nUNION ALL\n'.join(ends))
        sql = jreplace(sql, '@eraconstructorpad', str(nn(expression.collapse_settings, 'collapseSettings').era_pad))
        sql = jreplace(sql, '@inclusionRuleTable', self._inclusion_rule_table_sql(expression))
        sql = jreplace(sql, '@inclusionImpactAnalysisByEventQuery', self._inclusion_analysis_query('#qualified_events', 0))
        sql = jreplace(sql, '@inclusionImpactAnalysisByPersonQuery', self._inclusion_analysis_query('#best_events', 1))
        cw = expression.censor_window
        sql = jreplace(sql, '@cohortCensoredStatsQuery',
                       template('cohortCensoredStats.sql')
                       if cw is not None and (not is_empty(cw.start_date) or not is_empty(cw.end_date)) else '')

        if options is not None:
            if options.cdm_schema is not None:
                sql = jreplace(sql, '@cdm_database_schema', options.cdm_schema)
            if options.target_table is not None:
                sql = jreplace(sql, '@target_database_schema.@target_cohort_table', options.target_table)
            if options.result_schema is not None:
                sql = jreplace(sql, '@results_database_schema', options.result_schema)
            if options.vocabulary_schema is not None:
                sql = jreplace(sql, '@vocabulary_database_schema', options.vocabulary_schema)
            elif options.cdm_schema is not None:
                sql = jreplace(sql, '@vocabulary_database_schema', options.cdm_schema)
            if options.cohort_id is not None:
                sql = jreplace(sql, '@target_cohort_id', str(options.cohort_id))
            sql = jreplace(sql, '@generateStats', '1' if options.generate_stats else '0')
            name = options.cohort_id_field_name if options.cohort_id_field_name is not None \
                else DEFAULT_COHORT_ID_FIELD_NAME
            sql = regex_replace_all(sql, '@cohort_id_field_name', name)
        else:
            sql = regex_replace_all(sql, '@cohort_id_field_name', DEFAULT_COHORT_ID_FIELD_NAME)
        return sql
