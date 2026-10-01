<p align="center"><img src="https://raw.githubusercontent.com/vitaldb/duckcdm/main/docs/logo.png" alt="duckcdm" width="320"></p>

# DuckCDM

**DuckCDM — DuckDB-powered OMOP CDM engine.** OMOP data. Simpler. Faster. Everywhere.

OHDSI SqlRender, Circe and Achilles plus an ATLAS 3.0-compatible WebAPI, in pure Python on DuckDB.
No Java, no Postgres, no Docker.

OMOP CDM · OHDSI ATLAS 호환 도구를 DuckDB 위에서, 순수 파이썬으로.

```
pip install duckcdm
```

| 모듈 | 원본 (OHDSI, Apache 2.0) | 상태 |
|---|---|---|
| `duckcdm.sqlrender` | SqlRender (Java) | 이식 완료 — Java 판과 58,440 건 글자 단위 일치 |
| `duckcdm.circe` | circe-be (Java) | 이식 완료 — Java 판과 32,937 건 비교, 결과가 다른 것 0 건(PhenotypeLibrary 1,104 코호트 포함) |
| `duckcdm.webapi` | WebAPI 3.0 (Java) — ATLAS 3.0 화면이 부르는 API | 어휘 검색·개념집합·코호트 정의·생성·포함규칙 보고서·데이터 소스 보고서. ATLAS 3.0 화면을 함께 담았다 |
| `duckcdm.achilles` | Achilles (R) | 보고서용 분석 110개를 원본 SQL 그대로 DuckDB 에서 (Eunomia 2초) |

```python
from duckcdm.sqlrender import render, translate
sql = render("SELECT TOP 10 * FROM @cdm.person {@adult}?{WHERE year_of_birth < 2000};", cdm="main", adult=True)
translate(sql, "duckdb")   # SELECT  * FROM main.person WHERE year_of_birth < 2000 LIMIT 10;
```

```python
from duckcdm.circe import build_cohort_query
sql = build_cohort_query(open("cohort.json").read(), cdm_schema="main", target_table="main.cohort", cohort_id=1)
duck = translate(render(sql), "duckdb")
```

```
pip install "duckcdm[server]"
duckcdm serve EUNOMIA=cdm.duckdb --achilles # http://127.0.0.1:8080/ 에 ATLAS 3.0, /WebAPI 에 API
duckcdm achilles cdm.duckdb                 # 데이터 소스 보고서용 Achilles 결과만 계산
duckcdm cohort cohort.json duckdb --cdm main --results main --cohort-id 1
duckcdm translate query.sql duckdb -p cdm=main
duckcdm dialects
```

## 서버 (`duckcdm serve`)

DuckDB 파일 하나가 WebAPI 의 소스 하나(CDM·어휘·결과 스키마). 결과 스키마(`results`)와 코호트 결과 테이블은
없으면 만든다. 개념집합·코호트 정의는 첫 DB 옆 `duckcdm_store.duckdb` 에 저장한다. 로그인은 없다(관리자 한 명).
데이터 소스 보고서는 `--achilles`(또는 `duckcdm achilles`)로 결과 스키마에 Achilles 결과를 만든 뒤 나온다.
아직 없는 것: 특성화·발생률·경로 분석, 버전 이력, 태그.

## Java 판과의 동등성 검증

Circe: `tests/equiv/circe_corpus.py`(PhenotypeLibrary 코호트×옵션 3종, 개념집합, circe 테스트 JSON, 무작위 코호트) →
`tests/java_circe/CirceHarness.java`(Jackson 2.11·commons-lang3·standardized-analysis-utils·semver4j jar 필요) 와
`tests/equiv/py_circe_harness.py` 결과를 `tests/equiv/circe_diff.py` 로 비교.

SqlRender:

`tests/equiv/` — SqlRender R 테스트 문자열 2,009개, Circe·WebAPI SQL 763개(×16 방언·렌더·분할),
합성 조건식 3,000개를 Java 원본(`tests/java`, JDK 22+ 소스 실행)과 이 이식본에 넣어 결과를 비교한다.

```
python3 tests/equiv/build_corpus.py <클론 디렉터리> cases.tsv
python3 tests/equiv/run_java.py cases.tsv java.out 24
PYTHONPATH=src python3 tests/equiv/py_harness.py cases.tsv py.out 32
python3 tests/equiv/diff.py cases.tsv java.out py.out
```

알려진 차이: Java 내부 예외(EmptyStack, ArrayIndexOutOfBounds)의 메시지 문구만 다르다(예외가 나는 자리는 같다).
BMP 밖 문자(이모지 등)는 Java 가 UTF-16 대리쌍 둘로 보므로 위치가 달라질 수 있다.

## 라이선스

Apache 2.0. `src/duckcdm/sqlrender/csv/replacementPatterns.csv` 와 `ref/java`, `tests/java/org` 는
OHDSI SqlRender 에서 가져왔다(`third_party/`, `NOTICE`).
