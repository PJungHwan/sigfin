"""기준 저장/이전 입력 호환/전체 결과 해시 비교. API·DB 접근 없음."""

from dataclasses import replace
import json

import pytest

from backtest.experiments import ExperimentError, execute_experiment, experiment_json, result_digest
from backtest.experiment_verification import (
    RESULT_DIGEST_ALGORITHM, experiment_artifact_json, read_experiment_artifact,
    reference_from_result, verify_result,
)
from test_experiments import capture


def test_saved_artifact_preserves_exact_original_snapshot_and_compares_after_restart():
    original = execute_experiment(capture(split_at=2))
    exported = experiment_artifact_json(original)
    snapshot, reference = read_experiment_artifact(exported)
    assert experiment_json(snapshot) == experiment_json(original.snapshot)
    # 원본 Session State 없이 저장 파일만으로 재실행한다.
    replay = execute_experiment(snapshot)
    verification = verify_result(replay, reference, source="저장 JSON")
    assert len(replay.runs) == 12
    assert verification.status == "MATCH"
    assert verification.actual_digest == verification.expected_digest == result_digest(original)


def test_legacy_input_still_loads_and_missing_reference_is_unavailable():
    saved = capture()
    snapshot, reference = read_experiment_artifact(experiment_json(saved))
    assert snapshot.input_digest == saved.input_digest and reference is None
    assert verify_result(execute_experiment(snapshot), reference).status == "UNAVAILABLE"


def test_reference_mismatch_is_visible_and_not_rebased_on_export():
    result = execute_experiment(capture())
    reference = reference_from_result(result).model_copy(update={"result_digest": "0" * 64})
    assert verify_result(result, reference).status == "MISMATCH"
    _, retained = read_experiment_artifact(experiment_artifact_json(result, reference=reference))
    assert retained.result_digest == reference.result_digest
    assert verify_result(result, retained).status == "MISMATCH"


def test_equal_metrics_do_not_hide_different_trade_events_in_hash():
    original = execute_experiment(capture())
    reference = reference_from_result(original)
    run = original.runs[0]
    events = run.result.events.copy(deep=True)
    events.loc[0, "reason"] = "TEST_EVENT_DIFFERENCE"
    changed_run = replace(run, result=replace(run.result, events=events))
    changed = replace(original, runs=(changed_run, *original.runs[1:]))
    assert changed.runs[0].result.metrics == original.runs[0].result.metrics
    assert verify_result(changed, reference).status == "MISMATCH"


@pytest.mark.parametrize("field,value", [("input_digest", "f" * 64),
                                        ("algorithm", "unsupported-version"),
                                        ("execution_status", "PARTIAL")])
def test_different_inputs_algorithms_or_failed_reference_are_unavailable(field, value):
    original = execute_experiment(capture())
    reference = reference_from_result(original).model_copy(update={field: value})
    assert verify_result(original, reference).status == "UNAVAILABLE"


def test_failed_replay_is_not_reported_as_match():
    original = execute_experiment(capture())
    failed_run = replace(original.runs[0], result=None, error="TEST_FAILURE")
    failed = replace(original, runs=(failed_run, *original.runs[1:]))
    assert failed.status == "PARTIAL"
    assert verify_result(failed, reference_from_result(original)).status == "UNAVAILABLE"


@pytest.mark.parametrize("modification", ["bad_hash", "wrong_input", "unknown_version", "extra_field"])
def test_invalid_artifact_is_rejected(modification):
    result = execute_experiment(capture())
    payload = json.loads(experiment_artifact_json(result))
    if modification == "bad_hash": payload["reference"]["result_digest"] = "bad"
    if modification == "wrong_input": payload["reference"]["input_digest"] = "f" * 64
    if modification == "unknown_version": payload["artifact_schema_version"] = 2
    if modification == "extra_field": payload["extra"] = "unsupported"
    with pytest.raises(ExperimentError):
        read_experiment_artifact(json.dumps(payload))


@pytest.mark.parametrize("text", ['{bad', '{"bad":NaN}',
                                '{"artifact_schema_version":1,"artifact_schema_version":1}'])
def test_invalid_json_and_duplicate_fields_are_rejected(text):
    with pytest.raises(ExperimentError): read_experiment_artifact(text)
