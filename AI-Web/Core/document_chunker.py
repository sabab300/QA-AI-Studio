"""
QA AI Studio
Smart Document Chunker
Version: 3.0
"""

import re


class DocumentChunker:

    def __init__(
        self,
        chunk_size=1200,
        overlap=200
    ):

        self.chunk_size = chunk_size
        self.overlap = overlap

    # --------------------------------------------------
    # Split Document
    # --------------------------------------------------

    def split(self, text):

        if not text:
            return []

        # Normalize
        text = text.replace("\r", "")
        text = re.sub(r"\n{3,}", "\n\n", text)

        paragraphs = [

            p.strip()

            for p in text.split("\n\n")

            if p.strip()

        ]

        chunks = []

        current = ""

        for paragraph in paragraphs:

            # Large paragraph
            if len(paragraph) > self.chunk_size:

                if current:

                    chunks.append(current.strip())
                    current = ""

                start = 0

                while start < len(paragraph):

                    end = start + self.chunk_size

                    chunks.append(
                        paragraph[start:end]
                    )

                    start = end - self.overlap

                continue

            if len(current) + len(paragraph) < self.chunk_size:

                current += paragraph + "\n\n"

            else:

                chunks.append(current.strip())

                overlap_text = current[-self.overlap:]

                current = overlap_text + "\n\n" + paragraph + "\n\n"

        if current.strip():

            chunks.append(current.strip())

        return chunks

    # --------------------------------------------------

    def statistics(self, chunks):

        return {

            "total_chunks": len(chunks),

            "chunk_size": self.chunk_size,

            "overlap": self.overlap

        }