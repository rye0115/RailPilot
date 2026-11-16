"""Centralized official URLs and selectors.

Selectors change; keep them here so a page update is a one-file fix.
"""

BASE = "https://kyfw.12306.cn"
LOGIN_URL = f"{BASE}/otn/resources/login.html"
LEFT_TICKET_URL = f"{BASE}/otn/leftTicket/init"
ORDER_URL = f"{BASE}/otn/queryOrder/queryMyOrderNoComplete"
STATION_JS_URL = f"{BASE}/otn/resources/js/framework/station_name.js"
QUERY_URL = f"{BASE}/otn/leftTicket/queryG"
INIT_MY_12306_URL = f"{BASE}/otn/index/initMy12306Api"
CHECK_USER_URL = f"{BASE}/otn/login/checkUser"

# Left-ticket page selectors (used only when a page-based fallback is needed).
FROM_INPUT = "#fromStationText"
TO_INPUT = "#toStationText"
DATE_INPUT = "#train_date"
QUERY_BUTTON = "#query_ticket"
RESULT_ROWS = "#queryLeftTable tr"

SEAT_NAMES = (
    "商务座",
    "特等座",
    "一等座",
    "二等座",
    "高级软卧",
    "软卧",
    "动卧",
    "硬卧",
    "软座",
    "硬座",
    "无座",
)

# Row indexes in the official queryG pipe-separated result. Verified against
# 2026-11 results and the public endpoint notes.
SEAT_INDEX = {
    "商务座": 32,
    "特等座": 25,
    "一等座": 31,
    "二等座": 30,
    "高级软卧": 21,
    "软卧": 23,
    "动卧": 33,
    "硬卧": 28,
    "软座": 24,
    "硬座": 29,
    "无座": 26,
}
