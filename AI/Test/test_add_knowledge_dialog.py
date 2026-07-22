import customtkinter as ctk

from Core.text_extractor import TextExtractor
from Core.knowledge_analyzer import KnowledgeAnalyzer
from App.UI.add_knowledge_dialog import AddKnowledgeDialog


ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

app = ctk.CTk()

app.geometry("300x100")

extractor = TextExtractor()

text = extractor.extract_text("requirements.txt")

analysis = KnowledgeAnalyzer().analyze(text)


def open_dialog():

    AddKnowledgeDialog(app, analysis)


ctk.CTkButton(
    app,
    text="Open Add Knowledge Dialog",
    command=open_dialog
).pack(pady=30)

app.mainloop()