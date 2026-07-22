import customtkinter as ctk


class AddKnowledgeDialog(ctk.CTkToplevel):

    def __init__(self, parent, analysis):

        super().__init__(parent)

        self.analysis = analysis
        self.saved = False

        self.title("Add Knowledge")
        self.geometry("900x650")

        self.grab_set()
        self.focus_force()

        # Main Frame
        main = ctk.CTkScrollableFrame(self)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        # Platform
        ctk.CTkLabel(
            main,
            text="Platform",
            anchor="w"
        ).pack(fill="x", pady=(5, 2))

        self.platform = ctk.CTkEntry(main, width=700)
        self.platform.insert(0, analysis.get("platform") or "")
        self.platform.pack(fill="x")

        # Category
        ctk.CTkLabel(
            main,
            text="Category",
            anchor="w"
        ).pack(fill="x", pady=(10, 2))

        self.category = ctk.CTkEntry(main, width=700)
        self.category.insert(0, analysis.get("category") or "")
        self.category.pack(fill="x")

        # Business Process
        ctk.CTkLabel(
            main,
            text="Business Process",
            anchor="w"
        ).pack(fill="x", pady=(10, 2))

        self.business_process = ctk.CTkEntry(main, width=700)
        self.business_process.insert(
            0,
            analysis.get("business_process") or ""
        )
        self.business_process.pack(fill="x")

        # Tags
        ctk.CTkLabel(
            main,
            text="Tags",
            anchor="w"
        ).pack(fill="x", pady=(10, 2))

        self.tags = ctk.CTkTextbox(
            main,
            height=80
        )
        self.tags.pack(fill="x")
        self.tags.insert(
            "1.0",
            ", ".join(analysis.get("tags", []))
        )

        # Summary
        ctk.CTkLabel(
            main,
            text="Summary",
            anchor="w"
        ).pack(fill="x", pady=(10, 2))

        self.summary = ctk.CTkTextbox(
            main,
            height=120
        )
        self.summary.pack(fill="both", expand=True)

        self.summary.insert(
            "1.0",
            analysis.get("summary") or ""
        )

        confidence = round(
            analysis.get("confidence", 0) * 100,
            1
        )

        self.result_label = ctk.CTkLabel(
            main,
            text=f"AI Confidence : {confidence}%"
        )
        self.result_label.pack(pady=15)

        ctk.CTkButton(
            main,
            text="Save Knowledge",
            height=40,
            command=self.save
        ).pack(fill="x", pady=(5, 15))

    def save(self):

        self.analysis["platform"] = self.platform.get().strip()

        self.analysis["category"] = self.category.get().strip()

        self.analysis["business_process"] = self.business_process.get().strip()

        self.analysis["summary"] = self.summary.get(
            "1.0",
            "end"
        ).strip()

        self.analysis["tags"] = [
            x.strip()
            for x in self.tags.get("1.0", "end").split(",")
            if x.strip()
        ]

        self.saved = True

        self.result_label.configure(
            text="✅ Knowledge saved successfully.",
            text_color="green"
        )

        self.after(1000, self.destroy)