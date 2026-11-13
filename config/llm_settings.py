"""공식 문서에서 확인한 표준 텍스트 가격(USD / 1M tokens). 자동 가격 추측 없음."""

from datetime import date

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini-2026-03-17"
SUPPORTED_OPENAI_MODELS = {"gpt-5.4-mini", DEFAULT_OPENAI_MODEL}
MODEL_PRICES = {
    model: {"input": .75, "output": 4.50, "verified_on": date(2026, 10, 15),
            "source": "https://developers.openai.com/api/docs/models/gpt-5.4-mini"}
    for model in SUPPORTED_OPENAI_MODELS
}
PRICE_MAX_AGE_DAYS = 30
AI_TIMEOUT_SECONDS = 30
AI_MAX_INPUT_BYTES = 24000
AI_MAX_OUTPUT_TOKENS = 5000
AI_DEFAULT_OUTPUT_TOKENS = 3000
AI_DUPLICATE_SECONDS = 60
AI_PROMPT_VERSION = "step9-grounded-v1"
