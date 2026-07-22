from Core.web_fetcher import WebFetcher
from Core.html_parser import HTMLParser

fetcher = WebFetcher()
parser = HTMLParser()

page = fetcher.get("https://example.com")

print(parser.extract(page["text"]))