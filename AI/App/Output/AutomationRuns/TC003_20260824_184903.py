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

import requests

# Define the API endpoint URL
url = "https://sit.psw.gov.pk/qa/esb/api/bankGuaranteeReport"

# Define headers with placeholders for dynamic values
headers = {"X-Token": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjBEOUY0RjI5MTg3MjlCMDk1RTZGNjkyQUZFOTIxQkVGMEJCQTVFMjJSUzI1NiIsInR5cCI6ImF0K2p3dCIsIng1dCI6IkRaOVBLUmh5bXdsZWIya3FfcEliN3d1NlhpSSJ9.eyJuYmYiOjE3ODc1NzYzMzcsImV4cCI6MTc4NzU3OTkzNywiaXNzIjoiaHR0cDovL3FhLnBzdy5nb3YucGsvYXV0aCIsImF1ZCI6WyJhdXRoIiwidXBzIl0sImNsaWVudF9pZCI6InBzdy5pbnQucGl0YiIsImNsaWVudF9jb2RlIjoiUElUQiIsImp0aSI6IjIyQUQ0MUYzNDQ3RkZCN0Y2MTc3RDNBMTE5OTBBNzhDIiwiaWF0IjoxNzg3NTc2MzM3LCJzY29wZSI6WyJhdXRoU2NvcGUiLCJ1cHNTY29wZSJdfQ.YGfqTYNiIKA9LuWZViXdRqc-5Ophuz_DVEZ-Z4EJxlejpXdMb_FGmGC8eyn1RlSIMAte_Ji2KY9Pq_gi_HJ-VXeSDjUDVGYyCzGeOxZiV-376YSwTyA-y3BTcQTIknpVFznQHJiauVXvG2MwgHm1k8Rt5EHUDGg6KQoYkXhdZ8fu1dxE9TNGvrhq-krQsaWu8YxDYKGbSz1YTM3sZiT3Vw8NutYh_dU6y9CmLPBQWgFAHqjuRBUusvQD0Yk6sIPpurPLFfXK3RczTcCMm4ulcU6ET3c7PTreKcNV7EZ9pDCCCv76XqOKxLblkMscH7nW2gup__HvgC8HmuHbeH1xQg",
"X-Channel-Id": "PITB-001",
"X-Operation": "PITB_REPORTS"
}

# Define the payload with required parameters
payload = {
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

# Send the POST request
response = requests.post(url, headers=headers, json=payload)

# Assert on the response status code
assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"

# Print the response content for verification
print(response.json())

try:
# Assertions based on expected fields in the response
assert "totalNoOfBankGuarantees" in response.json()["summary"], "Total bank guarantees not found"
assert "bankGuaranteeDetails" in response.json(), "Bank guarantee details not found"
except AssertionError as e:
print(f"Assertion failed