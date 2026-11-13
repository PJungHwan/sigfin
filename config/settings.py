APP_NAME = "Crypto Multi-Agent Research System"
APP_ICON = "📈"
APP_VERSION = "0.18.0"
DEBATE_DEFAULT_ROUNDS = 2
DEBATE_SUPPORTED_ROUNDS = (1, 2)

DEFAULT_SYMBOL = "BTC/USDT"
DEFAULT_TIMEFRAME = "1h"
DEFAULT_PERIOD = "3개월"

SUPPORTED_SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
]

SUPPORTED_TIMEFRAMES = [
    "5m",
    "15m",
    "1h",
    "4h",
    "1d",
]

# 달력 기준 기간: 월별 일수와 윤년을 반영한다.
PERIOD_OFFSETS = {
    "1주": {"weeks": 1},
    "1개월": {"months": 1},
    "3개월": {"months": 3},
    "6개월": {"months": 6},
    "1년": {"years": 1},
}
SUPPORTED_PERIODS = list(PERIOD_OFFSETS)

TIMEFRAME_MILLISECONDS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}

BINANCE_PAGE_LIMIT = 1000
MAX_CANDLES = 5000
MAX_FETCH_REQUESTS = 7
API_TIMEOUT_MS = 15000
DATA_CACHE_TTL_SECONDS = 60
DATA_CACHE_MAX_ENTRIES = 50

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

# 표시 한도 5,000개와 별도로 계산용 확정 봉을 최대 300개 추가 조회한다.
INDICATOR_WARMUP_CANDLES = 300
MAX_WARMUP_CANDLES = 300

# FRED는 버튼 조회만 수행한다. 동일 프로세스에서 요청 간격을 1초 이상 유지한다.
FRED_API_BASE_URL = "https://api.stlouisfed.org/fred"
FRED_TIMEOUT_SECONDS = 15
FRED_MIN_REQUEST_INTERVAL_SECONDS = 1.0
FRED_PAGE_LIMIT = 10000
FRED_MAX_OBSERVATIONS = 10000
FRED_MAX_REQUESTS_PER_SERIES = 3  # 메타데이터 1회 + 관측값 최대 2회
FRED_CACHE_TTL_SECONDS = 3600
FRED_CACHE_MAX_ENTRIES = 128
MACRO_PERIOD_YEARS = {"1년": 1, "3년": 3, "5년": 5, "10년": 10, "20년": 20}
