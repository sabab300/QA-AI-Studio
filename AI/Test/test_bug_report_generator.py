from Core.bug_report_generator import BugReportGenerator

generator = BugReportGenerator()

result = generator.generate(

    "Save button is not working on Edit Platform screen."

)

print(result)