"""STEP 18 저장 결과 지문과 재실행 지문 비교. 기존 입력/계산 해시 계약은 유지한다."""

from dataclasses import dataclass
import json
from typing import Literal

from pydantic import Field

from backtest.experiments import (
    ExperimentError, ExperimentSnapshot, experiment_from_json, result_digest,
    validate_experiment,
)
from llm.schemas import StrictModel
from services.research_snapshots import safe_json

# result_digest의 계약: 조합 정렬, 성과/종료 포지션/자산곡선/거래/이벤트/오류,
# pandas split JSON, UTC ns 날짜, double_precision=15. 실행 소요시간/임의 ID 제외.
RESULT_DIGEST_ALGORITHM = "experiment-result-sha256-split15-v1"


class ResultReference(StrictModel):
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm: str = Field(min_length=1, max_length=100)
    execution_status: Literal["COMPLETED", "PARTIAL", "FAILED"]


class ExperimentArtifact(StrictModel):
    """입력 원본을 감싼 다운로드 형식. 기존 ExperimentSnapshot은 변경하지 않는다."""

    artifact_schema_version: Literal[1] = 1
    snapshot: ExperimentSnapshot
    reference: ResultReference


@dataclass(frozen=True)
class ResultVerification:
    status: Literal["MATCH", "MISMATCH", "UNAVAILABLE"]
    reason: str
    actual_digest: str
    expected_digest: str | None
    source: str


def reference_from_result(experiment):
    return ResultReference(input_digest=experiment.snapshot.input_digest,
                           result_digest=result_digest(experiment),
                           algorithm=RESULT_DIGEST_ALGORITHM,
                           execution_status=experiment.status)


def verify_result(experiment, reference=None, *, source="기준 없음"):
    actual = result_digest(experiment)
    expected = reference.result_digest if reference is not None else None
    reason = None
    if reference is None:
        reason = "기존 입력 JSON에 기준 결과 해시가 없고 같은 입력의 원본 실행도 현재 세션에 없습니다."
    elif reference.input_digest != experiment.snapshot.input_digest:
        reason = "기준과 재실행의 입력 해시가 달라 동일 조건의 결과 비교가 불가능합니다."
    elif reference.algorithm != RESULT_DIGEST_ALGORITHM:
        reason = "기준 결과 해시의 계산 방식/버전이 현재 방식과 다릅니다."
    elif reference.execution_status != "COMPLETED" or experiment.status != "COMPLETED":
        reason = "기준 또는 재실행에 실패한 조합이 있어 완전한 재현 성공으로 판단하지 않습니다."
    if reason is not None:
        return ResultVerification("UNAVAILABLE", reason, actual, expected, source)
    matches = actual == expected
    return ResultVerification("MATCH" if matches else "MISMATCH",
                              "동일 입력의 모든 조합 결과 지문이 같습니다." if matches else
                              "동일 입력이지만 결과 지문이 다릅니다. 일부 주요 성과만 같아도 거래/이벤트/자산 곡선은 다를 수 있습니다.",
                              actual, expected, source)


def experiment_artifact_json(experiment, *, reference=None):
    """재실행 다운로드도 최초 기준을 보존해 불일치를 새 일치로 덮어쓰지 않는다."""
    saved = validate_experiment(experiment.snapshot)
    reference = reference_from_result(experiment) if reference is None else reference
    if reference.input_digest != saved.input_digest:
        raise ExperimentError("저장 기준과 실험 입력 해시가 다릅니다.")
    return safe_json(ExperimentArtifact(snapshot=saved, reference=reference))


def read_experiment_artifact(text):
    """기존 입력 JSON과 결과 기준을 포함한 새 JSON을 모두 읽는다. 자동 실행 없음."""
    if not isinstance(text, (str, bytes)) or len(text) > 8_000_000:
        raise ExperimentError("실험 JSON은 최대 8MB입니다.")

    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise ExperimentError("중복 JSON 필드는 허용하지 않습니다.")
            output[key] = value
        return output

    def reject_constant(_):
        raise ExperimentError("비유한 JSON 값입니다.")

    try:
        payload = json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
        safe_json(payload)
        if isinstance(payload, dict) and "artifact_schema_version" in payload:
            artifact = ExperimentArtifact.model_validate(payload)
            saved = validate_experiment(artifact.snapshot)
            if artifact.reference.input_digest != saved.input_digest:
                raise ExperimentError("저장 기준과 실험 입력 해시가 다릅니다.")
            return saved, artifact.reference
        return experiment_from_json(text), None
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise ExperimentError("저장 실험의 JSON/입력/결과 기준 검증에 실패했습니다.") from error
