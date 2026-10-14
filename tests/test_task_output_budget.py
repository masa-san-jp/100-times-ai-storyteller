from copy import deepcopy
from pathlib import Path

import pytest

from storyteller.adapter import AdapterError, AdapterResponse, AutoRunner, effective_output_tokens
from storyteller.cards import TaskCardError, generate_task_card
from storyteller.cache import cache_key
from storyteller.dev.dummy import create_dummy_orchestrator
from storyteller.validation import validate_document, load_yaml, SchemaValidationError
from tests.test_adapter import _model, OllamaAdapter, ollama_server, _OllamaHandler
from tests.test_openai_adapter import _model as openai_model, server, _Handler
from storyteller.openai_adapter import OpenAICompatibleAdapter
from storyteller.orchestrator import Orchestrator

ROOT = Path(__file__).parents[1]


def definition():
    return load_yaml(ROOT / 'harness/story/tasks/S8.detail.yaml')


@pytest.mark.parametrize('bad', [0, -1, True, 1.5, '2048'])
def test_invalid_limits_rejected(bad):
    task = definition()
    task['max_output_tokens'] = bad
    with pytest.raises(SchemaValidationError):
        validate_document(task, ROOT / 'schemas/task-definition.schema.json')
    with pytest.raises(TaskCardError, match='max_output_tokens'):
        generate_task_card(task, 'a' * 32, inputs={'event': {}})
    with pytest.raises(AdapterError, match='max_output_tokens'):
        effective_output_tokens(_model('http://localhost:11434'), bad)


def test_llm_only_and_cache_version():
    task = definition()
    assert validate_document(task, ROOT / 'schemas/task-definition.schema.json') == task
    code = {'id': 'S9.test', 'version': 1, 'kind': 'code', 'handler': 'test', 'max_output_tokens': 2048}
    with pytest.raises(SchemaValidationError):
        validate_document(code, ROOT / 'schemas/task-definition.schema.json')
    assert task['version'] == 6
    assert cache_key(task['id'], 4, {}, seed=1) != cache_key(task['id'], task['version'], {}, seed=1)
    assert '生成上限：2048トークン' in generate_task_card(task, 'a' * 32, inputs={'event': {}})


@pytest.mark.parametrize('limit', [None, 16, 100000])
def test_ollama_wire_limit_without_profile_mutation(ollama_server, limit, monkeypatch):
    model = _model(ollama_server)
    original = deepcopy(model)
    monkeypatch.setattr(_OllamaHandler, 'response', {'message': {'content': 'ok'}, 'done_reason': 'length'})
    response = OllamaAdapter(model).complete('本文', max_output_tokens=limit)
    assert response.done_reason == 'length'
    assert _OllamaHandler.requests[-1]['options']['num_predict'] == min(model.max_tokens, limit or model.max_tokens)
    assert model == original


@pytest.mark.parametrize('limit', [None, 16, 100000])
def test_openai_wire_limit_without_profile_mutation(server, limit):
    model = openai_model(server)
    original = deepcopy(model)
    _Handler.reply = {'choices': [{'message': {'content': '{'}, 'finish_reason': 'length'}]}
    response = OpenAICompatibleAdapter(model).complete('本文', max_output_tokens=limit)
    assert response.done_reason == 'length'
    assert response.content == '{'
    assert _Handler.requests[-1]['max_tokens'] == min(model.max_tokens, limit or model.max_tokens)
    assert model == original


@pytest.mark.parametrize('limit', [None, 2048])
def test_runner_passes_task_limit_only_when_declared(tmp_path, limit):
    orchestrator = create_dummy_orchestrator(tmp_path)
    definitions = deepcopy(orchestrator.task_definitions)
    task = definitions['D4.story']
    if limit is not None:
        task['max_output_tokens'] = limit
    task['inputs'] = {}
    orchestrator = Orchestrator(tmp_path, definitions, harness_kind='dummy',
                                harness_root=orchestrator.definition_root,
                                repository_root=orchestrator.repository_root)
    run = orchestrator.create_run(task_specs=[{'task_id': 'D4.story', 'type': 'D4.story'}], seed=1)
    calls = []
    class Adapter:
        def __init__(self, model):
            pass
        def complete(self, card, **kwargs):
            calls.append(kwargs)
            return AdapterResponse('確認できた項目です。', 'stop')
    AutoRunner(tmp_path, _model('http://localhost:11434'), orchestrator, adapter_factory=Adapter).run(run_id=run, workers=1, until_empty=True)
    assert calls
    for kwargs in calls:
        if limit is None:
            assert 'max_output_tokens' not in kwargs
        else:
            assert kwargs['max_output_tokens'] == limit


@pytest.mark.parametrize('bad', [0, -1, True, 1.5, '2048'])
@pytest.mark.parametrize('adapter_class,model_factory', [(OllamaAdapter, _model), (OpenAICompatibleAdapter, openai_model)])
def test_adapters_reject_invalid_limit_before_network(bad, adapter_class, model_factory):
    adapter = adapter_class(model_factory('http://localhost:1'))
    with pytest.raises(AdapterError, match='max_output_tokens'):
        adapter.complete('本文', max_output_tokens=bad)


def test_only_detail_task_declares_output_limit():
    tasks = [load_yaml(path) for path in (ROOT / 'harness').rglob('tasks/*.yaml')]
    capped = [task for task in tasks if 'max_output_tokens' in task]
    assert [(task['id'], task['max_output_tokens']) for task in capped] == [('S8.detail', 2048)]
