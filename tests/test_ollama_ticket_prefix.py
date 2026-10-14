from __future__ import annotations

from copy import deepcopy
from itertools import takewhile

import pytest

from storyteller.adapter import OllamaAdapter
from storyteller.cards import generate_task_card
from storyteller.openai_adapter import OpenAICompatibleAdapter
from storyteller.repair import generate_repair_card
from tests.test_adapter import _OllamaHandler, _model, ollama_server
from tests.test_cards import llm_task
from tests.test_openai_adapter import _Handler, _model as openai_model, server


def card_case(kind, ticket='a' * 32):
    task = llm_task()
    source = {'items': ['同じ素材の値']}
    schema = {'type': 'object', 'properties': {'text': {'type': 'string'}}}
    kwargs = {}
    if kind != 'json':
        task['output'] = 'text'
        schema = None
        task['card'].pop('output_example', None)
    if kind == 'number':
        task['element'] = 'number'
    if kind == 'repair':
        task['validate'] = {'checks': [{'min_chars': {'n': 40}}, {'max_chars': {'n': 80}}]}
        card = generate_repair_card(task, ticket, original_output='元の本文。', reason='min_chars', inputs=source)
    else:
        if kind == 'continuation':
            kwargs = {'continuation_tail': 'これまでの末尾。', 'extend_to_min': True}
        elif kind == 'retry':
            kwargs = {'retry_reason': '前回の理由。'}
        card = generate_task_card(task, ticket, inputs=source, **kwargs)
    return card, schema


@pytest.mark.parametrize('kind', ['json', 'text', 'number', 'repair', 'retry', 'continuation'])
def test_real_ollama_payload_moves_only_ticket_and_keeps_every_byte(ollama_server, kind):
    card, schema = card_case(kind)
    original = card
    model = _model(ollama_server)
    before = deepcopy(model)
    OllamaAdapter(model).complete(card, schema=schema)
    payload = _OllamaHandler.requests[-1]
    sent = payload['messages'][0]['content']
    first, body = card.split('\n', 1)
    assert sent == body + '\n' + first
    moved_body, moved_first = sent.rsplit('\n', 1)
    assert moved_first + '\n' + moved_body == original
    assert len(sent) == len(original) and card == original and model == before
    expected = {'model': model.name, 'messages': [{'role': 'user', 'content': sent}],
                'stream': False, 'options': {'temperature': model.temperature,
                'num_predict': model.max_tokens, 'num_ctx': model.context_length}}
    if schema is not None:
        expected['format'] = schema
    assert payload == expected


def test_two_tickets_retain_common_inference_prefix(ollama_server):
    cards = [card_case('retry', ticket)[0] for ticket in ('a' * 32, 'b' * 32)]
    for card in cards:
        OllamaAdapter(_model(ollama_server)).complete(card)
    sent = [payload['messages'][0]['content'] for payload in _OllamaHandler.requests[-2:]]
    common = lambda values: sum(1 for _ in takewhile(lambda pair: pair[0] == pair[1], zip(*values)))
    assert common(sent) > common(cards)
    assert sent[0].rsplit('\n', 1)[0] == sent[1].rsplit('\n', 1)[0]


@pytest.mark.parametrize('mutation', ['plain', 'missing', 'order', 'duplicate', 'bad-ticket', 'crlf'])
def test_unknown_card_http_payload_is_exactly_unchanged(ollama_server, mutation):
    card, _ = card_case('text')
    if mutation == 'plain':
        card = '任意の文字列。'
    elif mutation == 'missing':
        card = card.replace('## 守ること', '## 別の節')
    elif mutation == 'order':
        card = card.replace('## 入力', '## 仮').replace('## 手順', '## 入力').replace('## 仮', '## 手順')
    elif mutation == 'duplicate':
        card += '\n## 入力\n別の内容'
    elif mutation == 'bad-ticket':
        card = card.replace('# タスク ', '# 別の見出し ', 1)
    else:
        card = card.replace('\n', '\r\n')
    model = _model(ollama_server)
    OllamaAdapter(model).complete(card)
    assert _OllamaHandler.requests[-1] == {
        'model': model.name, 'messages': [{'role': 'user', 'content': card}], 'stream': False,
        'options': {'temperature': model.temperature, 'num_predict': model.max_tokens, 'num_ctx': model.context_length},
    }


@pytest.mark.parametrize('kind', ['json', 'text', 'number', 'repair', 'retry', 'continuation'])
def test_other_adapter_keeps_exact_original_card_payload(server, kind):
    card, schema = card_case(kind)
    _Handler.reply = {'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]}
    model = openai_model(server)
    OpenAICompatibleAdapter(model).complete(card, schema=schema)
    payload = _Handler.requests[-1]
    expected = {'model': model.name, 'messages': [{'role': 'user', 'content': card}],
                'stream': False, 'temperature': model.temperature, 'max_tokens': model.max_tokens}
    if schema is not None:
        expected['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'output', 'strict': True, 'schema': schema,
        }}
    assert payload == expected


def test_original_saved_card_and_claim_ticket_still_bind_submission(tmp_path, ollama_server):
    from storyteller.orchestrator import Orchestrator
    from tests.test_never_stop import llm
    task = llm('D1.value', 'text', [])
    harness = Orchestrator(tmp_path, {task['id']: task})
    run = harness.create_run(seed=1, input_data={'material': '同じ素材'})
    claim = harness.claim_next(run, executor_id='ollama.test')
    card_path = harness.task_dir(run, task['id']) / 'card.md'
    original = card_path.read_text(encoding='utf-8')
    assert original == claim['card'] and original.startswith('# タスク ' + claim['ticket'] + '\n')
    response = OllamaAdapter(_model(ollama_server)).complete(claim['card'])
    sent = _OllamaHandler.requests[-1]['messages'][0]['content']
    assert sent.endswith('# タスク ' + claim['ticket'])
    accepted = harness.submit(claim['ticket'], response.content)
    assert accepted.accepted
    assert card_path.read_text(encoding='utf-8') == original
    assert harness.load_run(run)['tasks'][task['id']]['state'] == 'done'
