from Core.web_fetcher import WebFetcher
from Core.html_parser import HTMLParser
from Core.content_extractor import ContentExtractor


fetcher = WebFetcher()
parser = HTMLParser()
extractor = ContentExtractor()


page = fetcher.get(
    "https://example.com"
)


text = parser.extract(
    page["text"]
)


clean = extractor.extract(
    text
)


print(clean)