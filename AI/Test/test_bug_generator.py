"""
QA AI Studio
Test Bug Report Generator
"""

from Core.bug_report_generator import BugReportGenerator

generator = BugReportGenerator()

result = generator.generate(
    issue="""
While creating a Single Declaration,
clicking Save throws HTTP 500 Internal Server Error.
"""
)

print("\n================ BUG REPORT GENERATOR ================\n")
print(result)