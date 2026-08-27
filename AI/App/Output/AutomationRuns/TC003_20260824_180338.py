# --- QA AI Studio: speed/timeout safety shim (auto-inserted at run time, not saved) ---
# Slows every action down by 300ms and raises the default
# wait timeout to 45000ms so a real application that renders or
# responds slower than Playwright's default pace doesn't cause
# intermittent failures. Adjust in QA Automation -> Test Environment
# Settings.
from playwright.sync_api import BrowserType as _QA_BrowserType
from playwright.sync_api import BrowserContext as _QA_BrowserContext

_QA_ORIGINAL_LAUNCH = _QA_BrowserType.launch


def _qa_launch_with_speed_settings(self, **kwargs):
    kwargs.setdefault("slow_mo", 300)
    return _QA_ORIGINAL_LAUNCH(self, **kwargs)


_QA_BrowserType.launch = _qa_launch_with_speed_settings

_QA_ORIGINAL_NEW_PAGE = _QA_BrowserContext.new_page


def _qa_new_page_with_timeout(self, *args, **kwargs):
    page = _QA_ORIGINAL_NEW_PAGE(self, *args, **kwargs)
    page.set_default_timeout(45000)
    page.set_default_navigation_timeout(45000)
    return page


_QA_BrowserContext.new_page = _qa_new_page_with_timeout
# --- end QA AI Studio shim ---

{
    "messageId": "a1374655-5eb8-4a0e-9eb5-989521cd1ca8",
    "timeStamp": "20260402120000",
    "senderId": "PITB",
    "receiverId": "PSW",
    "methodId": "2305",
    "signature": "uja9KhybKrrEYhDBC9RHLpk8KZXAB63CxNTk030Vzgk=",
    "data": {
        "bankCode": "ALL",
        "NTN": "",
        "traderName": "",
        "bgNo": "",
        "fromDate": "2026-05-22T19:00:00.000Z",
        "toDate": "2026-05-28T19:00:00.000Z",
        "agencyID": 15,
        "pagination": {}
    }
}