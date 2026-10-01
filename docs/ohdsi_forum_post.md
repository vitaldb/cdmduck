**Category:** Developers  ·  **Tags:** atlas, webapi, duckdb, sqlrender, circe

# DuckCDM: ATLAS 3.0 on DuckDB with a pure-Python WebAPI, SqlRender, Circe and Achilles

Hi all,

We'd like to share **DuckCDM — a DuckDB-powered OMOP CDM engine**, an open-source (Apache 2.0) project from Seoul National University Hospital.

DuckCDM runs the OHDSI analytics stack on a single DuckDB file or a folder of Parquet files, with no Java, PostgreSQL or Docker:

```
pip install "duckcdm[server]"
duckcdm serve EUNOMIA=eunomia.duckdb --achilles
```

…and the ATLAS 3.0 UI opens at http://127.0.0.1:8080/.

**What's in it**

| Module | Port of | Verification |
|---|---|---|
| `duckcdm.sqlrender` | SqlRender (Java) | Byte-identical output to the Java original on 58,440 cases (R test strings, 763 Circe/WebAPI SQL files × 16 dialects, render, split) |
| `duckcdm.circe` | circe-be | 32,937 cases compared with Java, 0 differences — includes all 1,104 PhenotypeLibrary cohorts × 3 option sets and 26,000 randomly generated cohort definitions |
| `duckcdm.achilles` | Achilles | Runs the 110 analyses behind the ATLAS data-source reports from Achilles' own SQL, translated to DuckDB (Eunomia: 2 s) |
| `duckcdm.webapi` | WebAPI 3.0 | FastAPI implementation of the endpoints ATLAS 3.0 calls: vocabulary, concept sets, cohort definitions, cohort generation with inclusion statistics, data-source reports. Ships the Atlas3 build |

All 1,104 PhenotypeLibrary cohorts generate and execute successfully on DuckDB (Eunomia).

Equivalence was tested by running the original Java code and the port on the same inputs and diffing the outputs character by character (harnesses are in the repo, `tests/equiv`). Java quirks are reproduced on purpose — Jackson coercion rules, HALF_UP formatting, HashMap iteration order in SqlRender's parameter substitution, etc.

**Why**

- Small sites, teaching, and quick feasibility work: ATLAS without standing up WebAPI + PostgreSQL.
- Parquet-based CDMs: `duckcdm views` maps a Parquet folder to OMOP views exposing only the standard CDM 5.3/5.4 columns.
- A Python-native SqlRender/Circe for pipelines that already live in Python.

We are now deploying it on our hospital-scale CDM (≈4 million persons, Parquet) behind our institutional research gateway, for approved researchers only.

**Not yet implemented:** characterization, incidence rates, pathways, version history, tags, and multi-user security (we rely on a gateway for authentication).

- Code: https://github.com/vitaldb/duckcdm
- PyPI: https://pypi.org/project/duckcdm/

Feedback is very welcome — especially from the Atlas3/WebAPI and HADES developers on API coverage and on where this could be useful to the community. Many thanks to everyone who built SqlRender, Circe, WebAPI, Atlas3 and Achilles; DuckCDM is a port of their work.

Hyung-Chul Lee
Seoul National University Hospital
