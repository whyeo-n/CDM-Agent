# DVS Specification Grammar

DVS(Data Validation Specifications) 문서의 Specifications 컬럼에 작성되는 문법 정의서.

---

## 1. 전체 구조

Specifications 컬럼은 **Statement Function** 하나로 구성된다.
DVS Type 컬럼과 Statement Function의 종류는 반드시 일치해야 한다.

```
SPEC := STATEMENT_FUNCTION "(" ARGS ")"
```

| DVS Type | 유효한 Statement Function |
|----------|--------------------------|
| System Query | `CHECK(...)` |
| Activate/Deactivate | `ACTIVATE(...)` 또는 `DEACTIVATE(...)` |
| Assign | `ASSIGN(...)` |
| Manual Review | 없음 |

---

## 2. Statement Functions

### CHECK — System Query
조건이 참일 때 쿼리를 발생시킨다.

```
CHECK(
    CONDITION_BLOCK
)
```

예시:
```
CHECK(
    this < AESTDAT
)

CHECK(
    AESER == 1
    AND AESDTH IS NULL
    AND AESLIFE IS NULL
)

CHECK(
    this[1] < 130 OR this[1] > 220
)

CHECK(
    MATH((SBP + 2 * DBP) / 3) > 100
)
```

---

### ACTIVATE / DEACTIVATE — Activate/Deactivate
조건이 참일 때 Item을 활성화/비활성화한다.

```
ACTIVATE(
    WHEN = CONDITION_BLOCK
)

DEACTIVATE(
    WHEN = CONDITION_BLOCK
)
```

예시:
```
ACTIVATE(
    WHEN = MHYN == 1
)

ACTIVATE(
    WHEN = V1.IE.IEYN == 1
    AND (
        V2.SV.VISDAT IS NOT NULL
        OR V2.IE.IEYN == 1
    )
)

DEACTIVATE(
    WHEN = DS.DS.DSTERM IS NOT NULL
    AND this.IsSaved
)
```

---

### ASSIGN — Assign
조건이 참일 때 값을 할당한다.

```
ASSIGN(
    VALUE = EXPRESSION,
    WHEN = CONDITION_BLOCK
)
```

예시:
```
ASSIGN(
    VALUE = CURRENT_DATE,
    WHEN = RNYN == 1
)

ASSIGN(
    VALUE = LNR.UNIT,
    WHEN = LNR IS NOT NULL
    AND LBORRES IS NOT NULL
)

ASSIGN(
    VALUE = AGE(BRTHDAT, EN.EN.RFICDAT),
    WHEN = BRTHDAT IS NOT NULL
    AND EN.EN.RFICDAT IS NOT NULL
)

ASSIGN(
    VALUE = BMI(WEIGHT, HEIGHT),
    WHEN = WEIGHT IS NOT NULL
    AND HEIGHT IS NOT NULL
)

ASSIGN(
    VALUE = MATH(WEIGHT / (HEIGHT ^ 2) * 10000),
    WHEN = WEIGHT IS NOT NULL
    AND HEIGHT IS NOT NULL
)
```

---

## 3. 표현식 컨텍스트 (Expression Context)

산술 연산자(`+`, `-`, `*`, `/`, `^`)는 반드시 **MATH() 또는 DATE() 안에서만** 사용해야 한다.
이 두 래퍼 바깥에서 산술 연산자가 나오면 문법 오류다.

| 래퍼 | 용도 | 허용 연산자 |
|------|------|------------|
| `MATH(...)` | 숫자 계산 | `+` `-` `*` `/` `^` 및 괄호 |
| `DATE(...)` | 날짜 계산 | `+` `-` 및 duration 생성자 |

---

### MATH — 숫자 표현식

숫자 연산이 필요한 경우 `MATH()` 로 감싼다.
내부는 숫자 표현식(MATH expression)으로 파싱된다.

```
MATH( numeric_expression )
```

예시:
```
MATH(WEIGHT / (HEIGHT ^ 2) * 10000)        # BMI 직접 계산
MATH(Q1 + Q2 + Q3 + Q4 + Q5)              # 설문 총점
MATH((SBP + 2 * DBP) / 3)                 # MAP 계산
MATH(LBORRES - LBSTNRLO)                   # 기준치 대비 차이
```

---

### DATE — 날짜 표현식

날짜 산술이 필요한 경우 `DATE()` 로 감싼다.
내부에서 `DAY()`, `MONTH()`, `YEAR()` 는 duration 생성자로 동작한다.

```
DATE( date_expression )
```

duration 생성자:

| 생성자 | 의미 |
|--------|------|
| `DAY(N)` | N일 |
| `MONTH(N)` | N개월 |
| `YEAR(N)` | N년 |

예시:
```
DATE(VISDAT - DAY(84))                     # 84일 전
DATE(VISDAT - YEAR(5))                     # 5년 전
DATE(VISDAT - DAY(123) + MONTH(3))         # 123일 빼고 3개월 더하기
DATE(EN.EN.RFICDAT - YEAR(2) - DAY(10))    # 2년 10일 전
```

CHECK 조건에서 사용:
```
CHECK(
    this < DATE(CURR.SV.VISDAT - DAY(84))
)

CHECK(
    this > DATE(V1.SV.VISDAT - YEAR(5))
)
```

---

## 4. 조건 (Condition)

여러 조건은 **줄바꿈 + AND/OR** 으로 연결한다.
괄호로 우선순위를 명시할 수 있다.

```
CONDITION_BLOCK := CONDITION
                 | CONDITION "\nAND" CONDITION_BLOCK
                 | CONDITION "\nOR"  CONDITION_BLOCK
                 | "(\n" CONDITION_BLOCK "\n)"
```

### 조건 유형

```
CONDITION := REFERENCE COMPARE_OP VALUE
           | REFERENCE COMPARE_OP MATH_EXPRESSION
           | DATE_EXPRESSION COMPARE_OP REFERENCE
           | REFERENCE "IS NULL"
           | REFERENCE "IS NOT NULL"
           | REFERENCE "IN" "[" VALUE_LIST "]"
           | REFERENCE "NOT IN" "[" VALUE_LIST "]"
           | REFERENCE "IN" REFERENCE ".Values"
           | REFERENCE "NOT IN" REFERENCE ".Values"
           | DOMAIN_FUNCTION COMPARE_OP VALUE
           | REFERENCE PROPERTY_STATE
```

### 비교 연산자

| 연산자 | 의미 | 사용 위치 |
|--------|------|-----------|
| `==` | 같음 | 조건문 |
| `!=` | 다름 | 조건문 |
| `<` | 미만 | 조건문 |
| `>` | 초과 | 조건문 |
| `<=` | 이하 | 조건문 |
| `>=` | 이상 | 조건문 |
| `=` | 할당 | `ASSIGN(VALUE = ...)` 에서만 |

> **주의:** 조건문에서 `=` 는 사용 불가. 반드시 `==` 를 사용해야 한다.
> **주의:** 집합 비교는 `IN` / `NOT IN` 을 사용한다. `=` 로 목록을 비교하면 오류다.
> **주의:** 산술 연산자(`+` `-` `*` `/` `^`)는 `MATH()` 또는 `DATE()` 안에서만 사용한다.

---

## 5. 참조 (Reference)

### 기본 참조

| 형식 | 의미 |
|------|------|
| `this` | 현재 Item |
| `ITEM_ID` | 같은 Page 내 Item |
| `DOMAIN.PAGE.ITEM_ID` | 다른 Domain/Page의 Item |
| `VISIT.DOMAIN.PAGE.ITEM_ID` | 특정 Visit의 Item |

### Visit 접두사

| 접두사 | 의미 |
|--------|------|
| `V1`, `V2`, `V3` ... | 특정 Visit 번호 |
| `CURR` | 현재 Visit |
| `PREV` | 직전 Visit |

### Item Seq 참조

반복 테이블(Log Form) 내 특정 행을 참조한다.
`[N]` 은 0-based index가 아닌 **Item Seq (1부터 시작)** 를 의미한다.

```
this[1]      # 현재 Item의 Item Seq = 1 행
this[2]      # 현재 Item의 Item Seq = 2 행
ITEM_ID[N]   # 같은 Page 내 Item의 N번째 행
```

### 수식어 (Modifier)

| 수식어 | 의미 |
|--------|------|
| `REFERENCE.Values` | 반복 테이블 전체 행의 값 목록 |
| `REFERENCE[REL]` | 관련 행(Related row)의 값. IN/NOT IN 과 함께 사용 |

### 테이블 행 수

```
TABLE_ID$$CUNT    # 해당 테이블의 현재 행 수
```

### Item 상태

| 상태 | 의미 |
|------|------|
| `this.IsActivated` | Item 활성화 여부 |
| `this.IsSaved` | Item 저장 여부 |
| `this.Created` | Item 생성 여부 |

---

## 6. 도메인 함수 (Domain Functions)

자주 사용되는 임상 계산 패턴을 함수로 제공한다.
도메인 함수로 표현되지 않는 계산은 `MATH()` 또는 `DATE()` 로 직접 표현한다.

### 집계 함수

| 함수 | 의미 |
|------|------|
| `ALL(REFERENCE)` | 참조 대상 전체가 조건을 만족 |
| `ANY(REFERENCE)` | 참조 대상 중 하나라도 조건을 만족 |
| `MAX(REFERENCE)` | 최댓값 |
| `MIN(REFERENCE)` | 최솟값 |
| `SUM(ref, ref, ...)` | 합산 (`MATH(Q1 + Q2 + ...)` 의 편의 표현) |

### 임상 지표 함수

| 함수 | 의미 |
|------|------|
| `AGE(birth_date, reference_date)` | 만 나이 (기본: MMDD 비교) |
| `AGE(birth_date, reference_date, "MONTH")` | 월만 비교, 같은 달 = 생일 지남 |
| `AGE(birth_date, reference_date, "MONTH_STRICT")` | 월만 비교, 같은 달 = 생일 안 지남 (보수적) |
| `BMI(weight_kg, height_cm)` | BMI 계산 |
| `EGFR_CKD_EPI(creatinine, age, sex)` | eGFR (CKD-EPI 공식) |
| `EGFR_MDRD(creatinine, age, sex)` | eGFR (MDRD 공식) |

#### AGE 부분 날짜 처리

`birth_date` 에 일(Day)이 없는 경우 (YYYY-MM 형식), 내부적으로 해당 월의 1일(MM-01)로 처리한다.

| 옵션 | 비교 기준 | 같은 월일 처리 | 결과 |
|------|-----------|--------------|------|
| 없음 (기본) | MMDD | 생일 지남 | 높은 나이 |
| `"MONTH"` | MM | 생일 지남 | 높은 나이 |
| `"MONTH_STRICT"` | MM | 생일 안 지남 | 낮은 나이 (보수적) |

---

## 7. 특수 값 (Special Values)

| 값 | 의미 |
|----|------|
| `CURRENT_DATE` | 오늘 날짜 |
| `RANDOM_NUMBER` | 무작위 배정 번호 |
| `PI_SIGNATURE` | 연구자 서명 |
| `TRUE` / `FALSE` | 논리값 |

---

## 8. 오류 유형 (Syntax Error Types)

| 오류 코드 | 설명 | 잘못된 예 | 올바른 예 |
|-----------|------|-----------|-----------|
| `ERR_ASSIGN_IN_CONDITION` | 조건문에 `=` 사용 | `ITEM = 1` | `ITEM == 1` |
| `ERR_ASSIGN_FOR_SET` | 집합 비교에 `=` 사용 | `ITEM = [1,2,3]` | `ITEM IN [1,2,3]` |
| `ERR_OPERATOR_OUTSIDE_CONTEXT` | MATH/DATE 밖에서 산술 연산자 사용 | `WEIGHT / HEIGHT` | `MATH(WEIGHT / HEIGHT)` |
| `ERR_MISSING_WHEN` | ACTIVATE/DEACTIVATE/ASSIGN에 WHEN 누락 | `ACTIVATE()` | `ACTIVATE(WHEN = ...)` |
| `ERR_MISSING_VALUE` | ASSIGN에 VALUE 누락 | `ASSIGN(WHEN = ...)` | `ASSIGN(VALUE = ..., WHEN = ...)` |
| `ERR_TYPE_MISMATCH` | DVS Type과 Statement Function 불일치 | Type=SQ, `ACTIVATE(...)` | Type=SQ, `CHECK(...)` |
| `ERR_TRAILING_WHITESPACE` | 줄 끝 공백 | `this != VISDAT ` | `this != VISDAT` |
| `ERR_LOWERCASE_OPERATOR` | AND/OR 소문자 | `item == 1 and ...` | `item == 1 AND ...` |
| `ERR_INVALID_FUNCTION` | 정의되지 않은 함수 사용 | `CALC(...)` | 정의된 함수만 사용 |
| `ERR_INVALID_DATE_DURATION` | DATE() 안에서 잘못된 duration 생성자 | `DATE(VISDAT - 84)` | `DATE(VISDAT - DAY(84))` |
