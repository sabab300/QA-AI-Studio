from Core.intent_detector import IntentDetector

detector = IntentDetector()

tests = [

    "Generate SQL for Single Declaration",

    "Create Selenium automation",

    "Generate API test cases",

    "Write bug report for login failure",

    "Generate test cases for Warehousing",

    "Explain SD Warehousing",

    "How to create Single Declaration?",

    "Describe PSW Warehousing"

]

for question in tests:

    intent = detector.detect(question)

    print(f"{question}  -->  {intent}")