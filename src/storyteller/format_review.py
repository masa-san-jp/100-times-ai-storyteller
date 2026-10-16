"""Scoped format reviews: deterministic targets, display, and quote checks."""
from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from .format_slides import check_slide


def review_id(part: str, number: int, name: str) -> str:
    return f"F4.review-{part}-b{number}-{name}"


def expand_reviews(checks: Sequence[Mapping[str, Any]], part: Mapping[str, Any],
                   story: Mapping[str, Any]) -> list[dict[str, Any]]:
    events = {event['id']: event for event in story['events']}
    people = {person['id']: person for person in story['cast']}
    reviews = []
    for check in checks:
        kind = check['kind']
        if kind == 'event_coverage':
            sources = [{key: events[event_id][key] for key in ('id', 'what', 'result')}
                       for event_id in part['event_ids']]
        elif kind == 'named_actor':
            sources = [{'characters': [{key: people[person['id']][key] for key in ('name', 'reading')}
                                       for person in part['characters']]}]
        elif kind == 'condition_conflict':
            sources = [{'condition': condition} for condition in dict.fromkeys(part.get('conditions', []))]
        else:
            sources = [{}]
        reviews.extend({**check, 'source': source} for source in sources)
    return reviews


def card_inputs(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Label valid Marp screen and narration without changing their text."""
    result = dict(inputs)
    if result.get('format') == 'slides':
        for key in ('body', 'body_once'):
            body = result.get(key)
            if isinstance(body, str) and not check_slide(body, {}):
                result[key] = ('【画面表示（見出し・本文）】\n' + body.replace(
                    '<!--', '\n【語り（スピーカーノート）】\n', 1).replace(
                    '-->', '\n【語りここまで】\n', 1))
    return result


def _source_strings(source: Any) -> list[str]:
    if isinstance(source, str):
        return [source]
    if isinstance(source, Mapping):
        return [text for value in source.values() for text in _source_strings(value)]
    if isinstance(source, list):
        return [text for value in source for text in _source_strings(value)]
    return []


def assess(review: Mapping[str, Any], judgement: Mapping[str, str] | None,
           body: str) -> tuple[str, str]:
    """Return passed/failed/unresolved/advisory; evidence is not semantic proof."""
    if judgement is None:
        return 'unresolved', '判定が省略されたため確認できていません'
    reason = judgement['reason']
    source = review['source']
    source_quote, body_quote = judgement['source_quote'], judgement['body_quote']
    if source_quote and not any(source_quote in text for text in _source_strings(source)):
        return 'unresolved', f'対象資料に引用がありません：{reason}'
    if body_quote and body_quote not in body:
        return 'unresolved', f'元本文に引用がありません：{reason}'
    answer = judgement['answer']
    if answer == 'unknown':
        return 'unresolved', reason
    if answer == 'no':
        return 'passed', reason
    kind = review['kind']
    if kind == 'event_coverage':
        if not source_quote or source_quote not in (source['what'], source['result']):
            return 'unresolved', f'対象の行動・結果の全文引用がありません：{reason}'
        if source_quote in body:
            return 'unresolved', f'欠落とされた全文が元本文に実在します：{reason}'
    elif kind == 'condition_conflict':
        if not source_quote or source_quote != source['condition'] or not body_quote:
            return 'unresolved', f'条件全文または本文の引用がありません：{reason}'
    elif kind == 'named_actor':
        subject = judgement['subject']
        allowed = {unicodedata.normalize('NFKC', value) for person in source['characters']
                   for value in person.values()}
        if (not subject or not body_quote or subject not in body_quote
                or unicodedata.normalize('NFKC', subject) in allowed):
            return 'unresolved', f'追加人物の根拠が対象と一致しません：{reason}'
    return ('advisory' if review['severity'] == 'advisory' else 'failed'), reason


def collect(part: Mapping[str, Any], name: str, outputs: Mapping[str, Any], body: str
            ) -> tuple[list[str], list[str], bool]:
    failures, warnings = [], []
    quality_only = True
    for number, review in enumerate(part['reviews'], 1):
        assessment, reason = assess(review, outputs.get(review_id(part['id'], number, name)), body)
        target = f"{review['kind']} {review['source']}"
        message = f'検査{number}「{target}」：{reason}'
        if assessment == 'failed':
            failures.append(message)
            quality_only = quality_only and review['kind'] in {'condition_conflict', 'replay'}
        elif assessment in {'unresolved', 'advisory'}:
            warnings.append(f'{assessment}: {message}')
    return failures, warnings, quality_only
