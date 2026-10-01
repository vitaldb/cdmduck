<p align="center"><img src="https://raw.githubusercontent.com/vitaldb/duckcdm/main/docs/logo.png" alt="duckcdm" width="320"></p>

# DuckCDM

**DuckCDM — DuckDB-powered OMOP CDM engine.** OMOP data. Simpler. Faster. Everywhere.

DuckCDM runs the OHDSI analytics stack on a single DuckDB file (or a folder of Parquet files):
OHDSI SqlRender, Circe and Achilles, plus an ATLAS 3.0-compatible WebAPI that serves the
ATLAS 3.0 user interface. Pure Python — no Java, no PostgreSQL, no Docker.

```
pip install "duckcdm[server]"
duckcdm serve EUNOMIA=cdm.duckdb --achilles     # ATLAS 3.0 at http://127.0.0.1:8080/, API at /WebAPI
```

| Module | Ported from (OHDSI, Apache 2.0) | Status |
|---|---|---|
| `duckcdm.sqlrender` | SqlRender (Java) | Complete — byte-identical to Java on 58,440 test cases, 16 dialects |
| `duckcdm.circe` | circe-be (Java) | Complete — 32,937 cases compared with Java, 0 differences (incl. all 1,104 PhenotypeLibrary cohorts) |
| `duckcdm.achilles` | Achilles (R) | The 110 analyses the ATLAS data-source reports read, run from Achilles' own SQL (Eunomia: 2 s) |
| `duckcdm.webapi` | WebAPI 3.0 (Java) | The endpoints ATLAS 3.0 calls: vocabulary search, concept sets, cohort definitions, cohort generation, inclusion-rule reports, data-source reports. Bundles the ATLAS 3.0 build |
| `duckcdm.omop` | CommonDataModel | Parquet folder → DuckDB views exposing only the standard OMOP CDM v5.3/5.4 columns |

## Python API

```python
from duckcdm.sqlrender import render, translate
sql = render("SELECT TOP 10 * FROM @cdm.person {@adult}?{WHERE year_of_birth < 2000};", cdm="main", adult=True)
translate(sql, "duckdb")   # SELECT  * FROM main.person WHERE year_of_birth < 2000 LIMIT 10;

from duckcdm.circe import build_cohort_query
sql = build_cohort_query(open("cohort.json").read(), cdm_schema="main", target_table="main.cohort", cohort_id=1)
duck = translate(render(sql), "duckdb")

import duckdb
from duckcdm.achilles import run_achilles
run_achilles(duckdb.connect("cdm.duckdb"), cdm_schema="main", results_schema="results")
```

## Command line

```
duckcdm serve EUNOMIA=cdm.duckdb --achilles   # ATLAS 3.0 + WebAPI; build Achilles results if missing
duckcdm views /data/cdm_parquet cdm.duckdb    # Parquet folder (one sub-folder per table) -> OMOP views
duckcdm achilles cdm.duckdb                   # Achilles results for the data-source reports only
duckcdm cohort cohort.json duckdb --cdm main --results main --cohort-id 1
duckcdm translate query.sql postgresql -p cdm=main
duckcdm dialects
```

`serve` and `achilles` accept `--memory-limit 8GB --threads 4` for shared servers.

## Server (`duckcdm serve`)

- One DuckDB file is one WebAPI *source* (CDM, Vocabulary and Results daimons).
  The results schema (`results`) and the cohort result tables are created when missing.
- Concept sets and cohort definitions are stored in `duckcdm_store.duckdb` next to the first database.
- The ATLAS 3.0 UI is served from `/` with a relative API URL, so it also works behind a reverse
  proxy under a path prefix.
- No login of its own. For multi-user deployments run it behind an authenticating reverse proxy with
  `--gateway`: requests are accepted only from the proxy's network (`--gateway-ip`), only when the proxy's
  marker header is present (`--gateway-marker`, default `X-Auth-Method=parent_gateway`), and only for user ids
  (from `X-Auth-User-ID` / `User-ID` / `X-Auth-Subject`) listed in `--allow-users FILE` (re-read on change).
- Data-source reports appear after Achilles results are built (`--achilles` or `duckcdm achilles`).
- Not yet implemented: characterization, incidence rates, pathways, version history, tags.

## Equivalence with the Java originals

SqlRender — `tests/equiv/`: 2,009 strings from the SqlRender R tests, 763 Circe/WebAPI SQL files
(× 16 dialects, render and split) and 3,000 synthetic conditionals, run through the Java original
(`tests/java`, run with the JDK 22+ source launcher) and this port:

```
python3 tests/equiv/build_corpus.py <clones dir> cases.tsv
python3 tests/equiv/run_java.py cases.tsv java.out 24
PYTHONPATH=src python3 tests/equiv/py_harness.py cases.tsv py.out 32
python3 tests/equiv/diff.py cases.tsv java.out py.out
```

Circe — `tests/equiv/circe_corpus.py` (PhenotypeLibrary cohorts × 3 option sets, their concept sets,
circe test JSON, random cohorts) through `tests/java_circe/CirceHarness.java` (needs Jackson 2.11,
commons-lang3, standardized-analysis-utils and semver4j jars) and `tests/equiv/py_circe_harness.py`,
compared with `tests/equiv/circe_diff.py`.

End to end — `tests/e2e/run_phenotypes.py` generates, translates and executes all 1,104
PhenotypeLibrary cohorts on DuckDB (Eunomia: 1,104/1,104 succeed).

Known differences: only the message text of Java-internal exceptions (EmptyStack,
ArrayIndexOutOfBounds) differs — the exceptions occur at the same inputs. Characters outside the BMP
(emoji) count as two UTF-16 units in Java, so positions may differ for them.

## License

Apache 2.0. Includes material from OHDSI SqlRender, circe-be, WebAPI, Atlas3, Achilles and
CommonDataModel (all Apache 2.0); see `NOTICE` and `third_party/`.
