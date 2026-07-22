import sys

sys.path.append("AI")

from Core.report_generator import create_test_case_report

test_cases = [
    {
        "scenario": "MOA Validation",
        "priority": "High",
        "test_case": "Verify valid MOA is accepted",
        "pre_conditions": "User is logged in",
        "steps": "1. Open Declaration\n2. Enter valid MOA\n3. Submit",
        "expected_result": "Declaration submitted successfully",
        "test_type": "Positive"
    },
    {
        "scenario": "MOA Validation",
        "priority": "High",
        "test_case": "Verify invalid MOA is rejected",
        "pre_conditions": "User is logged in",
        "steps": "1. Open Declaration\n2. Enter invalid MOA\n3. Submit",
        "expected_result": "Validation error is displayed",
        "test_type": "Negative"
    }
]

file = create_test_case_report(test_cases)

print(file)