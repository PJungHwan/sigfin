"""별도 Python 프로세스에서 프로젝트 모듈과 설정 import를 검사한다.

이미 실행 중인 Streamlit의 sys.modules나 데이터 캐시에 의존하지 않는다.
app.py는 실행하지 않고 해당 파일의 로컬 import 문을 검사한다.
"""

from pathlib import Path
import subprocess
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

IMPORT_CHECK = r'''
import ast
import importlib
from pathlib import Path

root = Path.cwd().resolve()
packages = sorted(
    path.name for path in root.iterdir()
    if path.is_dir() and (path / "__init__.py").is_file()
)
files = [root / "app.py"]
for package in packages:
    files.extend((root / package).rglob("*.py"))

checked_imports = 0
for path in sorted(files):
    # 서비스/수집/차트 등 모든 로컬 모듈 자체의 import도 검증한다.
    if path.name != "app.py":
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        importlib.import_module(".".join(parts))
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module or node.level:
            continue
        if node.module.split(".")[0] not in packages:
            continue
        module = importlib.import_module(node.module)
        source = Path(module.__file__).resolve()
        assert source.is_relative_to(root), f"Unexpected module location: {source}"
        for alias in node.names:
            assert hasattr(module, alias.name), (
                f"{path.relative_to(root)}: {node.module}.{alias.name} is missing"
            )
            checked_imports += 1

from config import settings
assert isinstance(settings.API_TIMEOUT_MS, int) and settings.API_TIMEOUT_MS > 0
assert settings.DEFAULT_SYMBOL in settings.SUPPORTED_SYMBOLS
assert settings.DEFAULT_TIMEFRAME in settings.SUPPORTED_TIMEFRAMES
assert settings.DEFAULT_PERIOD in settings.SUPPORTED_PERIODS
assert set(settings.SUPPORTED_TIMEFRAMES) == set(settings.TIMEFRAME_MILLISECONDS)
assert set(settings.SUPPORTED_PERIODS) == set(settings.PERIOD_OFFSETS)
assert 0 < settings.BINANCE_PAGE_LIMIT <= 1000
assert settings.MAX_CANDLES <= settings.BINANCE_PAGE_LIMIT * settings.MAX_FETCH_REQUESTS
assert 0 <= settings.INDICATOR_WARMUP_CANDLES <= settings.MAX_WARMUP_CANDLES
assert settings.MAX_CANDLES + settings.MAX_WARMUP_CANDLES <= settings.BINANCE_PAGE_LIMIT * settings.MAX_FETCH_REQUESTS
assert settings.DATA_CACHE_TTL_SECONDS > 0
assert settings.DATA_CACHE_MAX_ENTRIES > 0

from collectors.binance import _get_exchange
exchange = _get_exchange()
assert exchange.timeout == settings.API_TIMEOUT_MS
assert exchange.enableRateLimit
assert exchange.options["defaultType"] == "spot"
assert exchange.options["fetchMarkets"]["types"] == ["spot"]
assert not exchange.apiKey and not exchange.secret
print(f"IMPORT OK: {len(files)} source files, {checked_imports} local imports")
print(f"Settings source: {settings.__file__}")
print(f"API_TIMEOUT_MS: {settings.API_TIMEOUT_MS}")
'''


class ImportTests(unittest.TestCase):
    def test_all_local_imports_in_fresh_python_process(self):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", IMPORT_CHECK],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("IMPORT OK:", result.stdout)
        print(result.stdout.strip(), flush=True)


if __name__ == "__main__":
    unittest.main()
