"""실제 시장·사용자 저장소와 분리한 SIGFIN 합성 자료 시연."""

from threading import RLock

# Streamlit sessions are separate threads, but scoped source adapters are global.
# Serialize demo runs so one session cannot restore another session's patches.
SOURCE_LOCK = RLock()
