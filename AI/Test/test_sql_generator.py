"""
QA AI Studio
Test SQL Generator
"""

from Core.sql_generator import SQLGenerator

generator = SQLGenerator()

result = generator.generate(
    requirement="Verify Single Declaration is saved successfully."
)

print("\n================ SQL GENERATOR ================\n")
print(result)