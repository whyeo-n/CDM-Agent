---
paths:
  - "**/*.py"
---

# Naming & Documentation Rules

## 1. Full Term Priority (Clarity over Brevity)

- Avoid abbreviations. Use full terms to ensure explicit meaning.
- Bad: `auth_usr`, `calc_amt`, `msg_cnt`
- Good: `authenticated_user`, `calculate_amount`, `message_count`

## 2. Contextual Precision

- Names should clearly describe the role and nature of the data they handle.
- Bad: `data`, `process`, `temp`
- Good: `user_registration_metadata`, `process_transaction_refund`, `temporary_cache_file`

## 3. Avoid Single-Letter Variables

- Do not use single-letter variable names, except for conventional loop indices (`i`, `j`, `k`).

## 4. English Only for Comments

- All code comments and documentation must be written in English.

## 5. Exceptions: Industry Standards

- Standard industry acronyms are permitted: `ID`, `URL`, `HTML`, `API`, `SQL`.

## 6. Consistency

- Use consistent terminology throughout the project. Do not mix synonyms (e.g., `Update` vs. `Modify` vs. `Edit`).
