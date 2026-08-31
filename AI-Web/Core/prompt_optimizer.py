"""
QA AI Studio
Prompt Optimizer

Version: 2.0
Production Optimized
"""


from Config import settings



class PromptOptimizer:


    def __init__(self):


        self.max_prompt_length = getattr(

            settings,

            "MAX_PROMPT_LENGTH",

            12000

        )


        self.global_rules = """
GLOBAL QA AI RULES

- Use ONLY supplied Knowledge Base context.
- Never hallucinate.
- Never invent requirements.
- Never invent workflows.
- Never invent APIs.
- Never invent database schema.
- Never guess missing information.
- Keep response professional and structured.
"""



    # --------------------------------------------------
    # Prompt Type Rules
    # --------------------------------------------------

    def _type_rules(

        self,

        prompt_type

    ):


        rules = {


            "sql":

            """
SQL:
- Use only supplied schema.
- Never invent tables or columns.
""",



            "test_case":

            """
TEST CASE:
- Generate requirement based scenarios only.
- Avoid duplicate test cases.
- Cover valid and invalid scenarios.
""",



            "bug":

            """
BUG:
- Do not invent environment details.
- Use Not Provided when missing.
""",



            "api":

            """
API:
- Never invent endpoint, payload or authentication.
""",



            "automation":

            """
AUTOMATION:
- Use Selenium best practices.
- Use explicit waits.
- Avoid hardcoded sleep.
""",



            "answer":

            """
ANSWER:
- Provide direct answer.
- Mention limitations.
"""

        }


        return rules.get(

            prompt_type,

            ""

        )



    # --------------------------------------------------
    # Compress Prompt
    # --------------------------------------------------

    def _compress(

        self,

        prompt

    ):


        if len(prompt) <= self.max_prompt_length:

            return prompt



        return (

            prompt[:self.max_prompt_length]

            +

            "\n\n[Context truncated for performance]"

        )



    # --------------------------------------------------
    # Remove Duplicate Lines
    # --------------------------------------------------

    def _remove_duplicates(

        self,

        text

    ):


        seen = set()

        output = []


        for line in text.splitlines():


            key = line.strip().lower()


            if not key:

                continue


            if key in seen:

                continue


            seen.add(key)

            output.append(
                line
            )


        return "\n".join(output)



    # --------------------------------------------------
    # Optimize
    # --------------------------------------------------

    def optimize(

        self,

        prompt,

        prompt_type="answer"

    ):


        if not prompt:

            return ""



        optimized = f"""

{self.global_rules}

{self._type_rules(prompt_type)}

--------------------------------------------------

REQUEST

{prompt}

"""



        optimized = self._remove_duplicates(

            optimized

        )


        optimized = self._compress(

            optimized

        )


        return optimized.strip()