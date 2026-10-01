"""Python port of OHDSI SqlRender — output matches the Java version character for character.

    from duckcdm.sqlrender import render, translate
    sql = render("SELECT * FROM @cdm.person {@limit}?{LIMIT 10}", cdm="main", limit=True)
    sql = translate(sql, "duckdb")
"""
from ._java import JavaError
from ._render import check as check_render
from ._render import render_sql
from ._strings import split_sql
from ._translate import (check as check_translate, dialects, generate_session_id, set_replacement_patterns,
                         translate_single_statement_sql, translate_sql)

__all__ = ['render', 'translate', 'translate_single_statement', 'split_sql', 'render_sql', 'translate_sql',
           'translate_single_statement_sql', 'dialects', 'generate_session_id', 'set_replacement_patterns',
           'check_render', 'check_translate', 'JavaError']


def _value(v):
    """Value conversion as in R SqlRender: booleans become TRUE/FALSE, lists are joined with commas."""
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, (list, tuple, set)):
        return ','.join(_value(x) for x in v)
    return str(v)


def render(sql, **params):
    return render_sql(sql, list(params), [_value(v) for v in params.values()])


def translate(sql, target_dialect, temp_emulation_schema=None, session_id=None):
    return translate_sql(sql, target_dialect, session_id, temp_emulation_schema)


def translate_single_statement(sql, target_dialect, temp_emulation_schema=None, session_id=None):
    return translate_single_statement_sql(sql, target_dialect, session_id, temp_emulation_schema)
