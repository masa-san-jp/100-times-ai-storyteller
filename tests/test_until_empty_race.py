"""P4-01: do not exit when the last live claim just unlocked more work."""
import threading

import pytest

from storyteller.adapter import AdapterResponse, AutoRunner
from storyteller.orchestrator import Orchestrator, TaskSpec
from storyteller.manifest import write_manifest
from tests.test_auto_workers import _definition, _model


@pytest.mark.parametrize('report', [False, True])
@pytest.mark.parametrize('global_run', [False, True])
@pytest.mark.parametrize('code_bridge', [False, True])
def test_until_empty_survives_last_claim_becoming_ready(tmp_path, monkeypatch, report, global_run, code_bridge):
    data = tmp_path / 'data'
    definitions = {name: _definition(name) for name in ('D1.a', 'D1.b')}
    specs = [TaskSpec('D1.a', 'D1.a')]

    def expand(context):
        context.add_task(TaskSpec('D1.b', 'D1.b', deps=(context.task_id,)))
        return {'expanded': True}

    if code_bridge:
        definitions['D2.plan'] = {'id': 'D2.plan', 'version': 1, 'kind': 'code', 'handler': 'expand'}
        specs.append(TaskSpec('D2.plan', 'D2.plan', deps=('D1.a',)))
    else:
        specs.append(TaskSpec('D1.b', 'D1.b', deps=('D1.a',)))
    h = Orchestrator(data, definitions, {'expand': expand}, harness_kind='report' if report else 'story')
    directory = data / 'batches/b1/report' if report else None
    run = h.create_run(seed=1, task_specs=specs, directory=directory)
    parent = h.claim_next(run, executor_id='other')
    checked_empty = threading.Event()
    submitted = threading.Event()
    failures = []
    generated = []
    sleeps = []

    class FixedAdapter:
        def __init__(self, model):
            pass

        def complete(self, card, **kwargs):
            generated.append(card)
            return AdapterResponse('fixed child', 'stop')

    runner = AutoRunner(data, _model(), h, adapter_factory=FixedAdapter)
    original = runner._claim_next
    paused = False

    def pause_after_empty(rid):
        nonlocal paused
        result = original(rid)
        if result[0] is None and not paused:
            paused = True
            checked_empty.set()
            assert submitted.wait(5), 'other worker did not submit'
            assert not failures
        return result

    def finish_parent():
        try:
            assert checked_empty.wait(5), 'worker did not observe empty queue'
            assert h.submit(parent['ticket'], 'fixed parent').accepted
        except BaseException as error:
            failures.append(error)
        finally:
            submitted.set()

    monkeypatch.setattr(runner, '_claim_next', pause_after_empty)
    monkeypatch.setattr('storyteller.adapter.time.sleep', sleeps.append)
    other = threading.Thread(target=finish_parent)
    other.start()
    try:
        runner.run(run_id=None if global_run else run, workers=1, until_empty=True)
    finally:
        other.join(timeout=5)
    assert not other.is_alive() and not failures
    manifest = h.load_run(run)
    assert manifest['status'] == 'completed'
    assert manifest['tasks']['D1.b']['state'] == 'done'
    assert len(generated) == 1
    assert sleeps == [0.05]  # retain a polling delay; no spin on newly-ready work


def test_until_empty_exits_for_ready_task_restricted_to_another_executor(tmp_path, monkeypatch):
    h = Orchestrator(tmp_path, {'D1.a': _definition('D1.a')})
    run = h.create_run(seed=1)
    manifest = h.load_run(run)
    manifest['tasks']['D1.a']['executor'] = 'other'
    write_manifest(h.run_dir(run) / 'manifest.json', manifest)
    monkeypatch.setattr('storyteller.adapter.time.sleep', lambda _: pytest.fail('waited for ineligible task'))

    class NoInference:
        def __init__(self, model):
            pass

        def complete(self, *args, **kwargs):
            pytest.fail('claimed ineligible task')

    AutoRunner(tmp_path, _model(), h, adapter_factory=NoInference).run(
        run_id=run, workers=1, until_empty=True)
    assert h.load_run(run)['tasks']['D1.a']['state'] == 'ready'
