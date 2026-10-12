"""Lossless editing (ADR-0003): the round-trip corpus and property tests."""

from __future__ import annotations

import copy
import difflib
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from provider_builder import configedit as ce

ROOT = Path(__file__).resolve().parents[2]
HAND = sorted((Path(__file__).parent / 'fixtures' / 'corpus').glob('*.yml'))
VENDOR = [
    *sorted((ROOT / 'vendor' / 'pygeoapi' / 'tests').glob('pygeoapi-test-*config*.yml')),
    ROOT / 'vendor' / 'pygeoapi-geoconnex-examples' / 'pygeoapi.config.yml',
    ROOT / 'vendor' / 'pygeoapi-iow' / 'docker' / 'default.config.yml',
    ROOT / 'vendor' / 'pygeoapi-plugins' / 'tests' / 'pygeoapi-test-config.yml',
    ROOT / 'docker' / 'pygeoapi' / 'pygeoapi.config.yml',
]
CORPUS = HAND + [p for p in VENDOR if p.is_file()]
IDS = [p.relative_to(ROOT).as_posix() for p in CORPUS]


def read(path: Path) -> str:
    return path.read_bytes().decode('utf-8')


def changed_old_lines(before: str, after: str) -> list[int]:
    a, b = before.splitlines(), after.splitlines()
    out: list[int] = []
    for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != 'equal':
            out.extend(range(i1, max(i2, i1 + 1)))
    return out


def counts(before: str, after: str) -> tuple[int, int]:
    diff = ce.unified_diff(before, after).splitlines()
    minus = sum(1 for d in diff if d.startswith('-') and not d.startswith('---'))
    plus = sum(1 for d in diff if d.startswith('+') and not d.startswith('+++'))
    return minus, plus


def resource_lines(text: str, rid: str) -> range:
    """The 0-based line span of a resource, as the editor's own ruamel parse sees it."""
    p = ce._Patcher(text.lstrip('﻿').replace('\r\n', '\n'))
    line, col, end = p.key_span(p.rt['resources'], rid)
    return range(line, end + 1)


def test_corpus_is_large_enough() -> None:
    assert len(HAND) >= 4
    if (ROOT / 'vendor' / 'pygeoapi').is_dir():
        assert len(CORPUS) >= 20


@pytest.mark.parametrize('path', CORPUS, ids=IDS)
def test_noop_save_is_byte_identical(path: Path) -> None:
    text = read(path)
    edit = ce.apply(text, ce.load(text.lstrip('﻿')))
    assert edit.changed is False
    assert edit.text is text


@pytest.mark.parametrize('path', CORPUS, ids=IDS)
def test_scalar_title_edit_changes_one_line(path: Path) -> None:
    text = read(path)
    for rid, res in ce.resources(ce.load(text.lstrip('﻿'))).items():
        if not isinstance(res.get('title'), str):
            continue
        new = copy.deepcopy(res)
        new['title'] = 'A new title'
        out = ce.set_resources(text, {rid: new}).text
        assert counts(text, out) == (1, 1), rid
        assert ce.resources(ce.load(out.lstrip('﻿')))[rid]['title'] == 'A new title'


@pytest.mark.parametrize('path', CORPUS, ids=IDS)
def test_delete_touches_only_the_resource(path: Path) -> None:
    text = read(path)
    doc = ce.load(text.lstrip('﻿'))
    res = ce.resources(doc)
    if len(res) < 2:
        pytest.skip('needs two resources')
    for rid in res:
        out = ce.set_resources(text, {rid: None}).text
        minus, plus = counts(text, out)
        assert plus == 0, rid
        span = resource_lines(text, rid)
        allowed = set(range(span.start - 1, span.stop + 1))
        assert set(changed_old_lines(text, out)) <= allowed, rid
        expected = copy.deepcopy(doc)
        del expected['resources'][rid]
        assert ce.same(ce.load(out.lstrip('﻿')), expected)


@pytest.mark.parametrize('path', CORPUS, ids=IDS)
def test_append_provider_only_adds_lines(path: Path) -> None:
    text = read(path)
    for rid, res in ce.resources(ce.load(text.lstrip('﻿'))).items():
        if not res.get('providers'):
            continue
        new = copy.deepcopy(res)
        new['providers'].append({'type': 'tile', 'name': 'MVT-proxy', 'data': 'https://x/{z}'})
        out = ce.set_resources(text, {rid: new}).text
        minus, plus = counts(text, out)
        assert minus == 0, rid
        assert plus == 3, rid


@pytest.mark.parametrize('path', CORPUS, ids=IDS)
def test_delete_then_add_round_trips_the_data(path: Path) -> None:
    text = read(path)
    doc = ce.load(text.lstrip('﻿'))
    for rid, res in ce.resources(doc).items():
        gone = ce.set_resources(text, {rid: None}).text
        back = ce.set_resources(gone, {rid: res}).text
        assert ce.same(ce.load(back.lstrip('﻿')), doc), rid


# ------------------------------------------------------------------ styles


def test_quotes_and_comments_are_kept() -> None:
    text = read(Path(__file__).parent / 'fixtures' / 'corpus' / 'anchors-and-blocks.yml')
    res = ce.resources(ce.load(text))['plain']
    new = copy.deepcopy(res)
    new['title'] = "It's quoted"
    new['description'] = 'Still "double"'
    out = ce.set_resources(text, {'plain': new}).text
    assert "    title: 'It''s quoted'   # keep the quotes\n" in out
    assert '    description: "Still \\"double\\""\n' in out
    assert '# trailing comment about logging' in out


def test_edit_next_to_a_merge_key() -> None:
    text = read(Path(__file__).parent / 'fixtures' / 'corpus' / 'anchors-and-blocks.yml')
    res = ce.resources(ce.load(text))['with-merge']
    new = copy.deepcopy(res)
    new['providers'][0]['id_field'] = 'fid'
    out = ce.set_resources(text, {'with-merge': new}).text
    assert counts(text, out) == (1, 1)
    assert '<<: *common' in out


def test_block_scalar_is_rewritten_and_noted() -> None:
    text = read(Path(__file__).parent / 'fixtures' / 'corpus' / 'anchors-and-blocks.yml')
    res = ce.resources(ce.load(text))['with-merge']
    new = copy.deepcopy(res)
    new['description'] = 'One line now'
    edit = ce.set_resources(text, {'with-merge': new})
    assert counts(text, edit.text) == (3, 1)
    assert [n.path for n in edit.notes] == ['/resources/with-merge/description']


def test_zero_offset_sequences_keep_their_style() -> None:
    text = read(Path(__file__).parent / 'fixtures' / 'corpus' / 'anchors-and-blocks.yml')
    res = ce.resources(ce.load(text))['plain']
    out = ce.set_resources(text, {'copy': res}).text
    assert '  copy:\n    type: collection\n' in out
    assert '    providers:\n    - type: feature\n      name: CSV\n' in out


def test_crlf_and_bom_survive() -> None:
    corpus = Path(__file__).parent / 'fixtures' / 'corpus'
    for name in ('crlf.yml', 'bom.yml'):
        text = read(corpus / name)
        res = ce.resources(ce.load(text.lstrip('﻿')))['a']
        new = copy.deepcopy(res)
        new['title'] = 'Changed'
        out = ce.set_resources(text, {'a': new}).text
        assert out.startswith('﻿') == text.startswith('﻿')
        assert ('\r\n' in out) == ('\r\n' in text)
        if '\r\n' in text:
            assert '\n' not in out.replace('\r\n', '')


def test_placeholders_stay_plain_when_edited() -> None:
    text = read(Path(__file__).parent / 'fixtures' / 'corpus' / 'envvars-and-nulls.yml')
    res = ce.resources(ce.load(text))['pg']
    new = copy.deepcopy(res)
    new['providers'][0]['data']['password'] = '${OTHER_PASSWORD}'
    out = ce.set_resources(text, {'pg': new}).text
    assert '                  password: ${OTHER_PASSWORD}\n' in out
    assert '                end: null  # ongoing\n' in out


def test_deleting_the_last_resource_leaves_an_empty_mapping() -> None:
    text = read(ROOT / 'docker' / 'pygeoapi' / 'pygeoapi.config.yml')
    rids = list(ce.resources(ce.load(text)))
    out = text
    for rid in rids:
        out = ce.set_resources(out, {rid: None}).text
    assert ce.load(out)['resources'] == {}


def test_unknown_resource_and_bad_yaml() -> None:
    with pytest.raises(ce.EditError):
        ce.set_resources('resources:\n  a: {}\n', {'b': None})
    with pytest.raises(ce.EditError):
        ce.set_resources('resources: [\n', {'b': {}})


# ------------------------------------------------------------------ merge3


def test_merge3_keeps_what_the_form_cannot_see() -> None:
    orig = {'title': 'T', 'visibility': 'hidden', 'extents': {'spatial': {'bbox': [0, 0, 1, 1]}}}
    base = {'title': 'T', 'extents': {'spatial': {'bbox': [0, 0, 1, 1], 'crs': 'CRS84'}}}
    ours = {'title': 'U', 'extents': {'spatial': {'bbox': [0, 0, 1, 1], 'crs': 'CRS84'}}}
    assert ce.merge3(orig, base, ours) == {
        'title': 'U',
        'visibility': 'hidden',
        'extents': {'spatial': {'bbox': [0, 0, 1, 1]}},
    }


def test_merge3_lists() -> None:
    orig = [{'a': 1, 'x': True}, {'b': 2}]
    base = [{'a': 1}, {'b': 2}]
    assert ce.merge3(orig, base, base) is orig
    assert ce.merge3(orig, base, [*base, {'c': 3}]) == [*orig, {'c': 3}]
    assert ce.merge3(orig, base, [{'b': 2}]) == [{'b': 2}]
    assert ce.merge3(orig, base, [{'a': 5}, {'b': 2}]) == [{'a': 5, 'x': True}, {'b': 2}]
    assert ce.merge3(orig, base, [{'b': 2}, {'a': 1}]) == [{'b': 2}, {'a': 1, 'x': True}]


def test_same_keeps_yaml_types_apart() -> None:
    assert not ce.same(1, True)
    assert not ce.same(1, 1.0)
    assert not ce.same('1', 1)
    assert ce.same({'a': [1, None]}, {'a': [1, None]})


# ------------------------------------------------------------------ properties

SCALARS = st.one_of(
    st.text(alphabet=st.characters(blacklist_categories=['Cs', 'Cc']), min_size=0, max_size=30),
    st.integers(-(10**6), 10**6),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.booleans(),
    st.none(),
    st.sampled_from(['yes', 'on', '1_000', '2020-01-01', '${X}', "it's", 'a: b', '# no', '- x']),
)


def scalar_paths(node: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    if isinstance(node, dict):
        return [p for k, v in node.items() for p in scalar_paths(v, (*prefix, k))]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in scalar_paths(v, (*prefix, i))]
    return [prefix]


def set_path(node: Any, path: tuple[Any, ...], value: Any) -> None:
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value


def del_path(node: Any, path: tuple[Any, ...]) -> None:
    for part in path[:-1]:
        node = node[part]
    del node[path[-1]]


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data())
def test_random_edits_touch_only_their_resource(data: st.DataObject) -> None:
    path = data.draw(st.sampled_from(CORPUS))
    text = read(path)
    doc = ce.load(text.lstrip('﻿'))
    res = ce.resources(doc)
    if not res:
        return
    rid = data.draw(st.sampled_from(sorted(res)))
    new = copy.deepcopy(res[rid])
    paths = [p for p in scalar_paths(new) if p]
    if not paths:
        return
    target = data.draw(st.sampled_from(paths))
    action = data.draw(st.sampled_from(['set', 'delete', 'add']))
    if action == 'set':
        set_path(new, target, data.draw(SCALARS))
    elif action == 'delete':
        del_path(new, target)
    else:
        parent: Any = new
        for part in target[:-1]:
            parent = parent[part]
        if isinstance(parent, dict):
            parent['zz_added'] = data.draw(SCALARS)
        else:
            parent.append(data.draw(SCALARS))
    try:
        out = ce.set_resources(text, {rid: new}).text
    except ce.EditError:
        return  # refusing is allowed; writing something else is not
    expected = copy.deepcopy(doc)
    expected['resources'][rid] = new
    assert ce.same(ce.load(out.lstrip('﻿')), expected)
    span = resource_lines(text, rid)
    allowed = set(range(span.start - 1, span.stop + 1))
    assert set(changed_old_lines(text, out)) <= allowed
