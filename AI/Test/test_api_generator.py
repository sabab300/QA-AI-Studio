"""
QA AI Studio
Test API Generator
"""

from Core.api_test_generator import APITestGenerator

generator = APITestGenerator()

result = generator.generate(
    api_description="Create Single Declaration API"
)

print("\n================ API TEST GENERATOR ================\n")
print(result)