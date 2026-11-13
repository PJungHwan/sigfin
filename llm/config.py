from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re

from dotenv import dotenv_values

from config.llm_settings import DEFAULT_OPENAI_MODEL

ENV_PATH = Path(__file__).resolve().parents[1]/".env"


class AIError(RuntimeError):
    """키·응답 원문·인증 헤더가 없는 표시용 예외."""

    def __init__(self, message, category="INPUT_VALIDATION", *, usage=None):
        super().__init__(message)
        self.category = category
        self.usage = usage


@dataclass(frozen=True)
class OpenAIConfig:
    model: str = DEFAULT_OPENAI_MODEL
    api_key: str | None = field(default=None, repr=False)

    @property
    def scope(self):
        return hashlib.sha256((self.api_key or "missing").encode()).hexdigest()


def read_openai_config():
    # 외부 경로/Streamlit secrets/OpenAI base URL 환경변수는 탐색하지 않는다.
    try:
        values = dotenv_values(ENV_PATH, interpolate=False) if ENV_PATH.is_file() else {}
    except (OSError, UnicodeError):
        raise AIError("프로젝트 .env의 UTF-8 인코딩과 읽기 권한을 확인하세요.", "CONFIGURATION") from None
    model = (os.getenv("OPENAI_MODEL") or values.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL).strip()
    key = (os.getenv("OPENAI_API_KEY") or values.get("OPENAI_API_KEY") or "").strip() or None
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", model):
        raise AIError("OPENAI_MODEL 형식을 확인하세요.", "CONFIGURATION")
    if key and (len(key) > 512 or any(char.isspace() for char in key)):
        raise AIError("OPENAI_API_KEY 형식을 확인하세요.", "CONFIGURATION")
    return OpenAIConfig(model, key)
