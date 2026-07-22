from Core.report_generator import create_test_case_report

sample = """
Test ID : TC-001

Title : Verify Login

Steps

1. Open Login Page

2. Enter Username

3. Enter Password

Expected Result

User should login successfully.
"""

file = create_test_case_report(sample)

print(file)