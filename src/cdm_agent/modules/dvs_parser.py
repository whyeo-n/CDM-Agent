import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class DVSParseError:
    error_code: str
    description: str
    original_text: str
    suggestion: str


@dataclass
class ParseResult:
    dvs_id: str
    is_valid: bool
    is_skipped: bool = False
    errors: list[DVSParseError] = field(default_factory=list)
    corrected_spec: str | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATEMENT_FUNCTIONS = {"CHECK", "ACTIVATE", "DEACTIVATE", "ASSIGN"}

VALID_STATEMENT_FUNCTIONS_BY_TYPE = {
    "System Query":           {"CHECK"},
    "Activate/Deactivate":    {"ACTIVATE", "DEACTIVATE"},
    "Assign":                 {"ASSIGN"},
    "Manual Review":          set(),
}

DOMAIN_FUNCTIONS = {
    "AGE", "BMI", "EGFR_CKD_EPI", "EGFR_MDRD",
    "ALL", "ANY", "MAX", "MIN", "SUM",
    "MATH", "DATE",
    "DAY", "MONTH", "YEAR",
}

ARITHMETIC_OPERATORS_PATTERN = re.compile(
    r'(?<![=!<>])'      # not preceded by =, !, <, >  (to avoid ==, !=, <=, >=)
    r'(?<![A-Z0-9_])'   # not preceded by identifier (to avoid being inside a token)
    r'[\+\-\*\/\^]'
    r'(?![=])'          # not followed by = (to avoid +=, -=, etc.)
)

# Pattern: IDENTIFIER = value (but NOT == and NOT inside THEN clause or VALUE= )
ASSIGN_IN_CONDITION_PATTERN = re.compile(
    r'\b([A-Z][A-Z0-9_\.$$\[\]]*)\s*(?<![=!<>])=(?!=)\s*(?!\s*\[)',
)

# Pattern: IDENTIFIER = [list]
ASSIGN_FOR_SET_PATTERN = re.compile(
    r'\b([A-Z][A-Z0-9_\.$$\[\]]*)\s*(?<![=!<>])=(?!=)\s*(\[[^\]]+\])',
)

TRAILING_WHITESPACE_PATTERN = re.compile(r'[ \t]+$', re.MULTILINE)

LOWERCASE_LOGICAL_PATTERN = re.compile(r'\b(and|or)\b')

DATE_WITHOUT_DURATION_PATTERN = re.compile(
    r'DATE\s*\([^)]*(?<![A-Z])\d+(?![A-Z_\(])[^)]*\)'
)


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _extract_statement_function(spec: str) -> str | None:
    """Extracts the outermost Statement Function name from a spec string."""
    match = re.match(r'^\s*([A-Z_]+)\s*\(', spec.strip())
    if match and match.group(1) in STATEMENT_FUNCTIONS:
        return match.group(1)
    return None


def _extract_outer_body(spec: str, function_name: str) -> str | None:
    """Extracts the body inside the outermost function call."""
    pattern = re.compile(rf'^\s*{function_name}\s*\((.+)\)\s*$', re.DOTALL)
    match = pattern.match(spec.strip())
    if match:
        return match.group(1).strip()
    return None


def _is_inside_math_or_date(position: int, spec: str) -> bool:
    """Checks whether a position in the spec is inside a MATH() or DATE() block."""
    depth = 0
    context_stack = []

    tokens = re.split(r'(\bMATH\s*\(|\bDATE\s*\(|[()])', spec)
    current_pos = 0

    for token in tokens:
        token_start = current_pos
        token_end = current_pos + len(token)

        if re.match(r'MATH\s*\(|DATE\s*\(', token):
            context_stack.append(depth + 1)
            depth += 1
        elif token == '(':
            depth += 1
        elif token == ')':
            if context_stack and depth == context_stack[-1]:
                context_stack.pop()
            depth -= 1

        if token_start <= position < token_end and context_stack:
            return True

        current_pos = token_end

    return False


def _remove_math_and_date_content(spec: str) -> str:
    """Replaces MATH() and DATE() content with placeholders for outer-context checks."""
    result = spec
    for wrapper in ("MATH", "DATE"):
        pattern = re.compile(rf'\b{wrapper}\s*\(')
        while True:
            match = pattern.search(result)
            if not match:
                break
            start = match.start()
            depth = 0
            end = match.end() - 1  # position of opening '('
            for index, char in enumerate(result[match.end() - 1:], start=match.end() - 1):
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth == 0:
                        end = index
                        break
            placeholder = wrapper + "(__PLACEHOLDER__)"
            result = result[:start] + placeholder + result[end + 1:]
    return result


# ---------------------------------------------------------------------------
# Error Detectors
# ---------------------------------------------------------------------------

def _detect_trailing_whitespace(spec: str) -> list[DVSParseError]:
    errors = []
    for line_number, line in enumerate(spec.split('\n'), 1):
        if re.search(r'[ \t]+$', line):
            errors.append(DVSParseError(
                error_code="ERR_TRAILING_WHITESPACE",
                description=f"Line {line_number}: trailing whitespace detected.",
                original_text=line,
                suggestion=line.rstrip(),
            ))
    return errors


def _detect_lowercase_operators(spec: str) -> list[DVSParseError]:
    errors = []
    for line_number, line in enumerate(spec.split('\n'), 1):
        match = LOWERCASE_LOGICAL_PATTERN.search(line)
        if match:
            corrected = LOWERCASE_LOGICAL_PATTERN.sub(lambda m: m.group().upper(), line)
            errors.append(DVSParseError(
                error_code="ERR_LOWERCASE_OPERATOR",
                description=f"Line {line_number}: logical operator '{match.group()}' must be uppercase.",
                original_text=line,
                suggestion=corrected,
            ))
    return errors


def _detect_assign_for_set(spec_without_context: str, original_spec: str) -> list[DVSParseError]:
    """Detects ITEM = [list] which should be ITEM IN [list]."""
    errors = []
    for match in ASSIGN_FOR_SET_PATTERN.finditer(spec_without_context):
        item_name = match.group(1)
        set_value = match.group(2)
        original_text = match.group(0)
        suggestion = f"{item_name} IN {set_value}"
        errors.append(DVSParseError(
            error_code="ERR_ASSIGN_FOR_SET",
            description=f"Set comparison must use IN, not '=': '{original_text}'",
            original_text=original_text,
            suggestion=suggestion,
        ))
    return errors


def _detect_assign_in_condition(spec_without_context: str, full_spec: str) -> list[DVSParseError]:
    """Detects bare = used as equality comparison (should be ==)."""
    errors = []
    # Skip lines that are THEN assignments (VALUE = ...) or WHEN = ...
    reserved_keywords = {"VALUE", "WHEN", "TRUE", "FALSE"}

    for match in ASSIGN_IN_CONDITION_PATTERN.finditer(spec_without_context):
        item_name = match.group(1)
        # Skip known parameter assignments
        if item_name in reserved_keywords:
            continue
        # Skip if it's inside a THEN IsActivated assignment
        if "IsActivated" in item_name:
            continue
        original_text = match.group(0)
        suggestion = original_text.replace("=", "==", 1)
        errors.append(DVSParseError(
            error_code="ERR_ASSIGN_IN_CONDITION",
            description=f"Use '==' for equality comparison, not '=': '{original_text.strip()}'",
            original_text=original_text,
            suggestion=suggestion,
        ))
    return errors


def _detect_operator_outside_context(spec: str) -> list[DVSParseError]:
    """Detects arithmetic operators (+, -, *, /, ^) used outside MATH() or DATE()."""
    errors = []
    cleaned = _remove_math_and_date_content(spec)

    # Remove known safe patterns: comparisons >=, <=, !=, ==
    safe_cleaned = re.sub(r'[><!]?=[>!]?', '__CMP__', cleaned)
    safe_cleaned = re.sub(r'[<>]', '__CMP__', safe_cleaned)

    # Find remaining arithmetic operators
    for match in re.finditer(r'(?<!\s)[+\-*/^](?!\s*[=>])|(?<=\s)[+\-*/^](?=\s)', safe_cleaned):
        errors.append(DVSParseError(
            error_code="ERR_OPERATOR_OUTSIDE_CONTEXT",
            description=f"Arithmetic operator '{match.group()}' must be inside MATH() or DATE().",
            original_text=match.group(),
            suggestion="Wrap the expression with MATH(...) or DATE(...)",
        ))
    return errors


def _detect_date_without_duration(spec: str) -> list[DVSParseError]:
    """Detects raw numbers inside DATE() without DAY()/MONTH()/YEAR() wrappers."""
    errors = []
    date_pattern = re.compile(r'\bDATE\s*\(([^)]+)\)', re.DOTALL)
    for match in date_pattern.finditer(spec):
        body = match.group(1)
        if re.search(r'(?<![A-Z_\(])\b\d+\b', body):
            errors.append(DVSParseError(
                error_code="ERR_INVALID_DATE_DURATION",
                description="Raw number inside DATE() must be wrapped with DAY(), MONTH(), or YEAR().",
                original_text=match.group(0),
                suggestion="Example: DATE(VISDAT - DAY(84)) instead of DATE(VISDAT - 84)",
            ))
    return errors


def _detect_missing_statement_function(dvs_type: str, spec: str) -> list[DVSParseError]:
    """Detects when a spec doesn't use the expected Statement Function wrapper."""
    errors = []
    detected_function = _extract_statement_function(spec)
    valid_functions = VALID_STATEMENT_FUNCTIONS_BY_TYPE.get(dvs_type, set())

    if not valid_functions:
        return errors  # Manual Review — no spec expected

    if detected_function is None:
        errors.append(DVSParseError(
            error_code="ERR_MISSING_STATEMENT_FUNCTION",
            description=f"Spec must be wrapped in a Statement Function: {sorted(valid_functions)}",
            original_text=spec[:80] + ("..." if len(spec) > 80 else ""),
            suggestion=f"Wrap with {next(iter(valid_functions))}(WHEN = ...) or similar.",
        ))
    elif detected_function not in valid_functions:
        errors.append(DVSParseError(
            error_code="ERR_TYPE_MISMATCH",
            description=f"DVS Type '{dvs_type}' requires {sorted(valid_functions)}, but found '{detected_function}'.",
            original_text=detected_function,
            suggestion=f"Use one of: {sorted(valid_functions)}",
        ))
    return errors


def _detect_missing_parameters(dvs_type: str, spec: str) -> list[DVSParseError]:
    """Detects missing WHEN= or VALUE= inside Statement Functions."""
    errors = []
    function_name = _extract_statement_function(spec)
    if function_name is None:
        return errors

    body = _extract_outer_body(spec, function_name) or ""

    if function_name in {"ACTIVATE", "DEACTIVATE"}:
        if "WHEN" not in body:
            errors.append(DVSParseError(
                error_code="ERR_MISSING_WHEN",
                description=f"{function_name}() requires a WHEN = ... parameter.",
                original_text=spec[:80],
                suggestion=f"{function_name}(\n    WHEN = <condition>\n)",
            ))

    if function_name == "ASSIGN":
        if "WHEN" not in body:
            errors.append(DVSParseError(
                error_code="ERR_MISSING_WHEN",
                description="ASSIGN() requires a WHEN = ... parameter.",
                original_text=spec[:80],
                suggestion="ASSIGN(\n    VALUE = <expression>,\n    WHEN = <condition>\n)",
            ))
        if "VALUE" not in body:
            errors.append(DVSParseError(
                error_code="ERR_MISSING_VALUE",
                description="ASSIGN() requires a VALUE = ... parameter.",
                original_text=spec[:80],
                suggestion="ASSIGN(\n    VALUE = <expression>,\n    WHEN = <condition>\n)",
            ))

    return errors


# ---------------------------------------------------------------------------
# Auto-corrector
# ---------------------------------------------------------------------------

THEN_CLAUSE_PATTERN = re.compile(r'\nTHEN\s+', re.IGNORECASE)


def _format_condition_as_when(condition: str) -> str:
    """Formats a raw condition block as a WHEN = ... clause (4-space indented)."""
    lines = [line.strip() for line in condition.strip().split('\n') if line.strip()]
    if not lines:
        return "    WHEN = "
    first_line = f"    WHEN = {lines[0]}"
    rest_lines = [f"    {line}" for line in lines[1:]]
    return '\n'.join([first_line] + rest_lines)


def _autocorrect_missing_statement_function(spec: str, dvs_type: str) -> str | None:
    """
    Attempts to wrap a spec that is missing its Statement Function.
    Handles the legacy THEN-based format used before grammar standardization.
    """
    then_match = THEN_CLAUSE_PATTERN.search(spec)

    if dvs_type == "System Query":
        indented_lines = '\n'.join(
            f"    {line.strip()}" for line in spec.strip().split('\n') if line.strip()
        )
        return f"CHECK(\n{indented_lines}\n)"

    elif dvs_type == "Activate/Deactivate":
        if then_match:
            condition = spec[:then_match.start()].strip()
            then_clause = spec[then_match.end():].strip()
            function_name = "DEACTIVATE" if "FALSE" in then_clause.upper() else "ACTIVATE"
        else:
            condition = spec.strip()
            function_name = "ACTIVATE"
        when_clause = _format_condition_as_when(condition)
        return f"{function_name}(\n{when_clause}\n)"

    elif dvs_type == "Assign":
        if not then_match:
            return None
        condition = spec[:then_match.start()].strip()
        then_clause = spec[then_match.end():].strip()
        value_match = re.match(r'this(?:\.\w+)?\s*=\s*(.+)', then_clause, re.IGNORECASE)
        value = value_match.group(1).strip() if value_match else then_clause
        when_clause = _format_condition_as_when(condition)
        return f"ASSIGN(\n    VALUE = {value},\n{when_clause}\n)"

    return None


def _autocorrect(spec: str, dvs_type: str, errors: list[DVSParseError]) -> str:
    corrected = spec

    for error in errors:
        if error.error_code == "ERR_MISSING_STATEMENT_FUNCTION":
            result = _autocorrect_missing_statement_function(corrected, dvs_type)
            if result is not None:
                corrected = result

        elif error.error_code == "ERR_TRAILING_WHITESPACE":
            corrected = re.sub(r'[ \t]+$', '', corrected, flags=re.MULTILINE)

        elif error.error_code == "ERR_LOWERCASE_OPERATOR":
            corrected = LOWERCASE_LOGICAL_PATTERN.sub(
                lambda match: match.group().upper(), corrected
            )

        elif error.error_code == "ERR_ASSIGN_FOR_SET":
            corrected = corrected.replace(error.original_text, error.suggestion, 1)

        elif error.error_code == "ERR_ASSIGN_IN_CONDITION":
            corrected = corrected.replace(error.original_text, error.suggestion, 1)

    return corrected


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(dvs_id: str, dvs_type: str, specification: str) -> ParseResult:
    """
    Parses a single DVS Specification entry and returns detected errors
    along with an auto-corrected version where possible.
    """
    if not specification or str(specification).strip() in ("", "nan") or dvs_type == "Manual Review":
        return ParseResult(dvs_id=dvs_id, is_valid=True, is_skipped=True)

    spec = str(specification).strip()
    errors: list[DVSParseError] = []

    # Cleaned spec with MATH/DATE content removed for outer-context checks
    spec_without_context = _remove_math_and_date_content(spec)

    # --- Run all detectors ---
    errors += _detect_trailing_whitespace(spec)
    errors += _detect_lowercase_operators(spec)
    errors += _detect_assign_for_set(spec_without_context, spec)
    errors += _detect_assign_in_condition(spec_without_context, spec)
    errors += _detect_operator_outside_context(spec)
    errors += _detect_date_without_duration(spec)
    errors += _detect_missing_statement_function(dvs_type, spec)
    errors += _detect_missing_parameters(dvs_type, spec)

    # --- Auto-correct where possible ---
    corrected_spec = _autocorrect(spec, dvs_type, errors) if errors else None

    # If corrections only changed auto-correctable errors and result equals original, clear it
    if corrected_spec == spec:
        corrected_spec = None

    return ParseResult(
        dvs_id=dvs_id,
        is_valid=len(errors) == 0,
        errors=errors,
        corrected_spec=corrected_spec,
    )
