"""A build turn crosses a real process boundary, and Stop or a timeout ends that process."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from second_brain.cloud_llm.pi import pi_helper

# Stands in for Node and the helper: echoes what it was given, or misbehaves on request.
HELPER = """\
import json, os, sys, time
from pathlib import Path
command, provider, model = sys.argv[1:]
context = json.load(sys.stdin)
here = Path(__file__).parent
if provider == 'slow':
    (here / 'started').write_text(str(os.getpid()))
    time.sleep(30)
if provider == 'failing':
    sys.exit(1)
if provider == 'garbled':
    print('not JSON: private data')
    sys.exit(0)
print(json.dumps({'outcome': 'stop', 'argv': sys.argv[1:], 'env': dict(os.environ),
                  'message': context['messages'][0] * 2}))
"""


@pytest.fixture
def helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "helper.py"
    script.write_text(HELPER)
    monkeypatch.setattr(pi_helper, "NODE", Path(sys.executable))
    monkeypatch.setattr(pi_helper, "PI_HELPER", script)
    return tmp_path


def test_a_turn_round_trips_more_than_a_pipe_holds_with_only_the_allowed_environment(
    helper: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key")
    monkeypatch.setenv("NODE_OPTIONS", "--require=/tmp/ambient.js")
    source = "x" * 300_000  # A pipe buffers 64 KiB, so both directions must be drained.

    turn = pi_helper.run_turn("vendor", "group/model", {"messages": [source]}, lambda: False)

    assert turn is not None
    assert turn["argv"] == ["build-turn", "vendor", "group/model"]
    assert turn["message"] == source * 2
    # Python adds a few variables of its own at startup; none of ours may come through.
    assert turn["env"].items() >= pi_helper.HELPER_ENV.items()
    assert not {"ANTHROPIC_API_KEY", "NODE_OPTIONS"} & turn["env"].keys()


@pytest.mark.parametrize("ending", ["stop", "timeout"])
def test_stop_or_a_timeout_ends_the_request_in_flight(
    helper: Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    monkeypatch.setattr(pi_helper, "_STOP_POLL_SECONDS", 0.05)
    started = helper / "started"
    if ending == "timeout":
        monkeypatch.setattr(pi_helper, "TURN_TIMEOUT_SECONDS", 0.5)
    began = time.monotonic()

    def stop_requested() -> bool:
        return ending == "stop" and started.exists()

    if ending == "stop":
        assert pi_helper.run_turn("slow", "model", {"messages": [""]}, stop_requested) is None
    else:
        with pytest.raises(pi_helper.PiHelperError, match="took too long"):
            pi_helper.run_turn("slow", "model", {"messages": [""]}, stop_requested)

    assert time.monotonic() - began < 10
    with pytest.raises(ProcessLookupError):
        os.kill(int(started.read_text()), 0)


@pytest.mark.parametrize("provider", ["failing", "garbled"])
def test_a_failed_or_garbled_helper_is_an_error_that_quotes_nothing(
    helper: Path, provider: str
) -> None:
    with pytest.raises(pi_helper.PiHelperError) as failure:
        pi_helper.run_turn(provider, "model", {"messages": [""]}, lambda: False)
    assert "private" not in failure.value.reason


def test_a_missing_runtime_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pi_helper, "NODE", tmp_path / "missing-node")
    with pytest.raises(pi_helper.PiHelperError, match="Could not start"):
        pi_helper.run_turn("vendor", "model", {}, lambda: False)


async def test_one_shot_commands_start_with_the_same_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "environment.py"
    script.write_text("import json, os\nprint(json.dumps(dict(os.environ)))\n")
    monkeypatch.setattr(pi_helper, "NODE", Path(sys.executable))
    monkeypatch.setattr(pi_helper, "PI_HELPER", script)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "ambient-token")

    environment = await pi_helper.run_json("list-models", "vendor")

    assert isinstance(environment, dict)
    assert environment.items() >= pi_helper.HELPER_ENV.items()
    assert "AWS_BEARER_TOKEN_BEDROCK" not in json.dumps(environment)
