import re
from collections import Counter


class KnowledgeAnalyzer:

    def __init__(self):

        self.platforms = {
            "WeBOC 2.0": [
                "weboc 2.0",
                "weboc2"
            ],

            "WeBOC": [
                "weboc",
                "customs automation"
            ],

            "PSW Core": [
                "pakistan single window",
                "psw",
                "single window"
            ],

            "PCS": [
                "pcs",
                "port community system"
            ],

            "ACS": [
                "acs",
                "automated customs"
            ]
        }


        self.categories = {

            "SRS": [
                "software requirement specification",
                "software requirements specification",
                "system requirement specification",
                "system requirements specification",
                "functional requirements",
                "non functional requirements",
                "business requirements",
                "use case",
                "scope of system",
                "system overview"
            ],


            "CRF": [
                "change request form",
                "change request",
                "crf",
                "enhancement request"
            ],


            "Test Case": [
                "test case",
                "test scenario",
                "expected result",
                "actual result",
                "pre-condition",
                "test steps"
            ],


            "API": [
                "swagger",
                "openapi",
                "endpoint",
                "rest api",
                "api specification",
                "request payload",
                "response payload",
                "http method"
            ],


            "Validation": [
                "validation rule",
                "business rule",
                "mandatory field",
                "required field",
                "field validation"
            ],


            "SOP": [
                "standard operating procedure",
                "procedure",
                "operational procedure",
                "workflow"
            ]

        }


        self.business_processes = [

            "SD Warehousing",
            "Single Declaration",
            "Goods Declaration",
            "Import",
            "Export",
            "Manifest",
            "Clearance",
            "Payment",
            "Risk Management",
            "Examination",
            "Release Order",
            "Transit",
            "Licensing"

        ]


        self.stop_words = {

            "this", "that", "with", "from",
            "have", "been", "were",
            "their", "there", "shall",
            "should", "would",
            "about", "into",
            "than", "then",
            "also", "only",
            "when", "where",
            "while", "which",
            "because", "without",
            "between",
            "document",
            "information",
            "figure",
            "table",
            "title",
            "page",
            "pages",
            "review",
            "history",
            "contents",
            "content",
            "chapter",
            "section"

        }



    # --------------------------------------------------

    def analyze(self, text):

        if not text:
            return self.default_result()


        text_lower = text.lower()


        document_type = self.detect_document_type(text_lower)


        platform = self.find_platform(text_lower)


        category = self.find_category(
            text_lower,
            document_type
        )


        business_process = self.find_business_process(text_lower)


        summary = self.generate_summary(text)


        tags = self.extract_tags(text)


        confidence = self.calculate_confidence(
            platform,
            category,
            business_process
        )


        return {

            "platform": platform,

            "category": category,

            "business_process": business_process,

            "document_type": document_type,

            "summary": summary,

            "tags": tags,

            "confidence": confidence

        }



    # --------------------------------------------------

    def detect_document_type(self, text):

        if (
            "software requirement" in text
            or "system requirement" in text
            or "functional requirement" in text
        ):
            return "SRS"


        if (
            "change request" in text
            or "crf" in text
        ):
            return "CRF"


        if (
            "test case" in text
            or "expected result" in text
            or "test scenario" in text
        ):
            return "Test Case"


        if (
            "swagger" in text
            or "endpoint" in text
            or "request payload" in text
        ):
            return "API"


        if (
            "standard operating procedure" in text
            or "procedure" in text
        ):
            return "SOP"


        return "General"



    # --------------------------------------------------

    def find_platform(self, text):

        scores = {}

        for platform, keywords in self.platforms.items():

            score = 0

            for keyword in keywords:

                if keyword in text:
                    score += 1


            if score:
                scores[platform] = score


        if scores:
            return max(
                scores,
                key=scores.get
            )


        return "Unknown"



    # --------------------------------------------------

    def find_category(
            self,
            text,
            document_type
    ):


        # Strong document classification

        if document_type != "General":
            return document_type



        scores = {}


        for category, keywords in self.categories.items():

            score = 0


            for keyword in keywords:

                if keyword in text:
                    score += 1


            if score:
                scores[category] = score



        if scores:

            return max(
                scores,
                key=scores.get
            )


        return "General"



    # --------------------------------------------------

    def find_business_process(self, text):

        matches = []


        for process in self.business_processes:

            if process.lower() in text:
                matches.append(process)



        if matches:

            return matches[0]


        return ""



    # --------------------------------------------------

    def generate_summary(self, text):

        lines = [

            line.strip()

            for line in text.split("\n")

            if len(line.strip()) > 100

        ]


        ignored = [

            "table of contents",

            "revision history",

            "document control",

            "approval"

        ]


        for line in lines:

            lower = line.lower()


            if any(
                item in lower
                for item in ignored
            ):
                continue


            return line[:500]



        return text[:500]



    # --------------------------------------------------

    def extract_tags(self, text):

        words = re.findall(
            r"[A-Za-z][A-Za-z0-9_-]{3,}",
            text
        )


        cleaned = []


        for word in words:

            lower = word.lower()


            if lower in self.stop_words:
                continue


            if lower.isdigit():
                continue


            cleaned.append(word)



        counts = Counter(cleaned)


        return [

            word

            for word, _ in counts.most_common(15)

        ]



    # --------------------------------------------------

    def calculate_confidence(
            self,
            platform,
            category,
            business_process
    ):

        score = 0.30


        if platform != "Unknown":
            score += 0.25


        if category != "General":
            score += 0.25


        if business_process:
            score += 0.20


        return round(
            min(score, 0.99),
            2
        )



    # --------------------------------------------------

    def default_result(self):

        return {

            "platform": "Unknown",

            "category": "General",

            "business_process": "",

            "document_type": "General",

            "summary": "",

            "tags": [],

            "confidence": 0.30

        }



def analyze_document(text):

    analyzer = KnowledgeAnalyzer()

    return analyzer.analyze(text)