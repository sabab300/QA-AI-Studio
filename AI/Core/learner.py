import os
from docx import Document
from pypdf import PdfReader


def read_docx(file_path):
    try:
        doc = Document(file_path)
        text = "\n".join([p.text for p in doc.paragraphs])
        return text
    except Exception as e:
        print(f"Error reading DOCX: {file_path}")
        print(e)
        return ""


def read_pdf(file_path):
    try:
        reader = PdfReader(file_path)

        text = ""

        for page in reader.pages:
            page_text = page.extract_text()

            if page_text:
                text += page_text + "\n"

        return text

    except Exception as e:
        print(f"Error reading PDF: {file_path}")
        print(e)
        return ""


def read_txt(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()

    except Exception as e:
        print(f"Error reading TXT: {file_path}")
        print(e)
        return ""


def extract_text(file_path):

    extension = os.path.splitext(file_path)[1].lower()

    if extension == ".docx":
        return read_docx(file_path)

    elif extension == ".pdf":
        return read_pdf(file_path)

    elif extension == ".txt":
        return read_txt(file_path)

    return ""


def scan_knowledge_folder():

    knowledge_path = os.path.join(os.path.dirname(__file__), "..", "Knowledge")
    knowledge_path = os.path.abspath(knowledge_path)

    documents = []

    print("\n==============================")
    print("SCANNING KNOWLEDGE FOLDER")
    print("==============================\n")

    for root, dirs, files in os.walk(knowledge_path):

        for file in files:

            if (
                file.lower().endswith((".docx", ".pdf", ".txt"))
                and not file.startswith("~$")
            ):

                file_path = os.path.join(root, file)

                print(f"Reading : {file}")

                text = extract_text(file_path)

                if text.strip():

                    documents.append({
                        "id": file,
                        "text": text
                    })

                    print(f"Characters : {len(text)}")

    print("\n==============================")
    print(f"Documents Loaded : {len(documents)}")
    print("==============================\n")

    return documents


if __name__ == "__main__":
    scan_knowledge_folder()