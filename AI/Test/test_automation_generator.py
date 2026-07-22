"""
QA AI Studio
Test Automation Generator
"""

from Core.automation_generator import AutomationGenerator

generator = AutomationGenerator()

result = generator.generate(
    api_description="Automate creation of Single Declaration using Selenium."
)

print("\n================ AUTOMATION GENERATOR ================\n")
print(result)