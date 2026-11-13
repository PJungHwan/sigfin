"""Session-local upload bytes survive navigation; never restore a click/event."""

from dataclasses import dataclass
import streamlit as st


@dataclass(frozen=True)
class SavedUpload:
    name: str
    data: bytes

    @property
    def size(self):
        return len(self.data)

    def getvalue(self):
        return self.data


def session_upload(label, *, type, key, max_upload_size):
    value = st.file_uploader(label, type=type, key=key, max_upload_size=max_upload_size)
    cache_key = '_ui_upload_' + key
    native_key = cache_key + '_native'
    run = st.session_state.get('_ui_run', 0)
    prior_run, prior_native = st.session_state.get(native_key, (-2, False))
    if value is not None:
        body = value.getvalue()
        if getattr(value, 'size', len(body)) > max_upload_size * 1_000_000:
            st.error('업로드 크기 제한을 초과했습니다.')
            st.session_state.pop(cache_key, None)
            return None
        st.session_state[cache_key] = SavedUpload(getattr(value, 'name', 'uploaded.json'), body)
    elif prior_run == run - 1 and prior_native:
        st.session_state.pop(cache_key, None)
    st.session_state[native_key] = (run, value is not None)
    saved = st.session_state.get(cache_key)
    if value is None and saved is not None:
        st.caption('이 세션에서 보관 중인 파일: ' + saved.name)
        if st.button('보관 파일 해제', key=cache_key + '_clear'):
            st.session_state.pop(cache_key, None)
            return None
    return value if value is not None else saved
