"""Python port of OHDSI circe-be — cohort definition (JSON) -> OHDSI SQL. Output matches the Java version character for character.

    from duckcdm.circe import build_cohort_query
    sql = build_cohort_query(cohort_json, cdm_schema="main", target_table="main.cohort", cohort_id=1)
"""
from ._jutil import CirceError
from ._model import CohortExpression, ConceptSetExpression
from ._query import (BuildExpressionQueryOptions, CohortExpressionQueryBuilder, build_concept_set_query)

__all__ = ['build_cohort_query', 'build_concept_set_query', 'CohortExpression', 'ConceptSetExpression',
           'CohortExpressionQueryBuilder', 'BuildExpressionQueryOptions', 'CirceError']

_builder = None


def build_cohort_query(expression, options=None, **kw):
    """expression: JSON string, dict or CohortExpression. options: BuildExpressionQueryOptions, dict, JSON, or keywords
    (cohort_id, cdm_schema, target_table, result_schema, vocabulary_schema, generate_stats, cohort_id_field_name)."""
    global _builder
    if _builder is None:
        _builder = CohortExpressionQueryBuilder()
    if kw:
        options = BuildExpressionQueryOptions(**kw)
    elif isinstance(options, (str, dict)):
        options = BuildExpressionQueryOptions.from_json(options)
    return _builder.build_expression_query(expression, options)
