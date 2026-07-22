import customtkinter as ctk
from tkinter import filedialog
import shutil
import sys
import os

# -----------------------------
# Import Core Modules
# -----------------------------
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Core.knowledge_manager import learn_knowledge
from Core.ask_ai import ask_ai

# -----------------------------
# Theme
# -----------------------------
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

# -----------------------------
# Main Window
# -----------------------------
app = ctk.CTk()
app.title("QA AI Assistant")
app.geometry("900x700")


# -----------------------------
# Status Label
# -----------------------------
status = ctk.CTkLabel(
    app,
    text="Status : Ready",
    font=("Arial", 14)
)


# -----------------------------
# Upload Document
# -----------------------------
def upload_document():

    popup = ctk.CTkToplevel(app)
    popup.title("Select Document Type")
    popup.geometry("350x220")
    popup.grab_set()

    ctk.CTkLabel(
        popup,
        text="Select Document Type",
        font=("Arial", 16, "bold")
    ).pack(pady=15)

    folder_map = {
        "Business Documents": "Business Documents",
        "SOPs": "SOPs",
        "Business Process": "Business_Process",
        "APIs": "APIs",
        "Validations": "Validations",
        "Test Cases": "Test_Cases",
        "Images": "Images",
        "Others": "Others"
    }

    selected = ctk.StringVar(value="Business Documents")

    combo = ctk.CTkComboBox(
        popup,
        values=list(folder_map.keys()),
        variable=selected,
        width=220
    )
    combo.pack(pady=10)

    def browse_file():

        popup.destroy()

        file = filedialog.askopenfilename(
            filetypes=[
                ("Documents", "*.pdf *.doc *.docx *.txt"),
                ("Images", "*.png *.jpg *.jpeg")
            ]
        )

        if not file:
            status.configure(text="Status : Cancelled")
            return

        folder = folder_map[selected.get()]

        destination_folder = os.path.join(
            "AI",
            "Knowledge",
            folder
        )

        os.makedirs(destination_folder, exist_ok=True)

        destination = os.path.join(
            destination_folder,
            os.path.basename(file)
        )

        shutil.copy2(file, destination)

        status.configure(
            text=f"Status : Saved in {folder}"
        )

    ctk.CTkButton(
        popup,
        text="Browse Document",
        command=browse_file
    ).pack(pady=20)


# -----------------------------
# Ask AI Function
# -----------------------------
def ask_question():

    question = question_box.get("1.0", "end").strip()

    if not question:
        return

    status.configure(text="Status : Searching...")

    answer = ask_ai(question)

    output_box.delete("1.0", "end")
    output_box.insert("end", answer)

    status.configure(text="Status : Ready")


# -----------------------------
# Title
# -----------------------------
title = ctk.CTkLabel(
    app,
    text="QA AI Assistant",
    font=("Arial", 28, "bold")
)
title.pack(pady=20)


# -----------------------------
# Question Box
# -----------------------------
question_box = ctk.CTkTextbox(
    app,
    width=750,
    height=80
)
question_box.pack(pady=10)


# -----------------------------
# Ask Button
# -----------------------------
ask_button = ctk.CTkButton(
    app,
    text="Ask AI",
    width=140,
    command=ask_question
)
ask_button.pack(pady=5)


# -----------------------------
# Feature Buttons
# -----------------------------
buttons = [
    ("📂 Upload Document", upload_document),
    ("🧠 Learn Knowledge", learn_knowledge),
    ("📝 Generate Test Cases", None),
    ("▶ Execute Test", None),
    ("📊 Reports", None)
]

for text, command in buttons:

    btn = ctk.CTkButton(
        app,
        text=text,
        width=300,
        height=40,
        command=command
    )

    btn.pack(pady=6)


# -----------------------------
# AI Output
# -----------------------------
output_label = ctk.CTkLabel(
    app,
    text="AI Output",
    font=("Arial", 16, "bold")
)
output_label.pack(pady=(15, 5))

output_box = ctk.CTkTextbox(
    app,
    width=820,
    height=180
)
output_box.pack(pady=5)


# -----------------------------
# Status
# -----------------------------
status.pack(side="bottom", pady=15)


# -----------------------------
# Run Application
# -----------------------------
app.mainloop()