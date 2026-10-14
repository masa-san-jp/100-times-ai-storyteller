"""Check the explicit actor of an event's action and result."""

from collections.abc import Mapping, Sequence
import re
import unicodedata
from typing import Any


def subject_errors(output: str, inputs: Mapping[str, Any]) -> list[str]:
    if inputs.get('event_field') not in {'何が起きたか', '結果', '出来事の結果'}:
        return []
    characters = inputs.get('characters')
    if not isinstance(characters, Sequence) or isinstance(characters, str):
        return []
    names = set()
    for character in characters:
        if not isinstance(character, Mapping) or not isinstance(character.get('name'), str):
            continue
        name = unicodedata.normalize('NFKC', character['name'])
        if name and name not in {'未定', '(未定)'}:
            names.add(name)
    if not names:
        return []
    literal = '(?:' + '|'.join(re.escape(name) for name in sorted(names, key=lambda name: (-len(name), name))) + ')'
    subject = re.compile(literal + r'\s*(?:[と、]\s*' + literal + r'\s*)*[はが]')
    opening = unicodedata.normalize('NFKC', output).lstrip()
    # Try the complete registered name before removing any enclosing quote.
    # A name may itself contain quotes, including its leading character.
    while opening and opening[0] in '「『"' and not subject.match(opening):
        opening = opening[1:].lstrip()
    if subject.match(opening):
        return []
    return ['event_subject: 冒頭の主語は割り当て人物 ' + '・'.join(sorted(names))
            + ' とし、単独名または共同の名前の後に「は」「が」を付けてください']
