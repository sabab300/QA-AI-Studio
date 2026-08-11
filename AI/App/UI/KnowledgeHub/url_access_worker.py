"""
QA AI Studio
URL Access Analyzer Worker
Development #2
"""

from PySide6.QtCore import QObject, Signal

from Core.url_access_analyzer import URLAccessAnalyzer


class URLAccessAnalyzerWorker(QObject):

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, url, headless=True):
        super().__init__()
        self.url = url
        self.headless = headless

    def run(self):
        try:
            analyzer = URLAccessAnalyzer(headless=self.headless)
            result = analyzer.analyze(self.url)
            self.finished.emit(result)
        except Exception as ex:
            self.error.emit(str(ex))