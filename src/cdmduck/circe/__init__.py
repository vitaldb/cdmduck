"""OHDSI circe-be 의 파이썬 이식 — 코호트 정의(JSON) → OHDSI SQL. Java 판과 출력이 글자 단위로 같다.

    from cdmduck.circe import build_cohort_query
    sql = build_cohort_query(cohort_json, cdm_schema="main", target_table="main.cohort", cohort_id=1)
"""
from ._jutil import CirceError
from ._model import CohortExpression, ConceptSetExpression
from ._query import (BuildExpressionQueryOptions, CohortExpressionQueryBuilder, build_concept_set_query)

__all__ = ['build_cohort_query', 'build_concept_set_query', 'CohortExpression', 'ConceptSetExpression',
           'CohortExpressionQueryBuilder', 'BuildExpressionQueryOptions', 'CirceError']

_builder = None


def build_cohort_query(expression, options=None, **kw):
    """expression: JSON 문자열·dict·CohortExpression. options: BuildExpressionQueryOptions·dict·JSON 또는 키워드
    (cohort_id, cdm_schema, target_table, result_schema, vocabulary_schema, generate_stats, cohort_id_field_name)."""
    global _builder
    if _builder is None:
        _builder = CohortExpressionQueryBuilder()
    if kw:
        options = BuildExpressionQueryOptions(**kw)
    elif isinstance(options, (str, dict)):
        options = BuildExpressionQueryOptions.from_json(options)
    return _builder.build_expression_query(expression, options)
