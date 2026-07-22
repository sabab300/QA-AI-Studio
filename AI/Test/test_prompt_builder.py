from Core.prompt_builder import PromptBuilder

builder = PromptBuilder()

print(

    builder.build_answer_prompt(

        "How to create Single Declaration?",

        "Sample QA Knowledge"

    )

)