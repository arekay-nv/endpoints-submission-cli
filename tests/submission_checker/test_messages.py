# SPDX-FileCopyrightText: Copyright (c) 2024 MLCommons
# SPDX-License-Identifier: Apache-2.0
"""The message catalog and the code that uses it must agree.

Every result names a rule and a message key and passes the values the message needs
(see :mod:`submission_checker.messages`). These tests read the checker's source to
hold both sides together: each key the code uses exists, each call passes exactly
the placeholders its template needs, and the catalog carries nothing the code no
longer produces.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

import submission_checker
from submission_checker import messages
from submission_checker.messages import SHARED, MessageCatalogError, placeholders

_SRC = Path(submission_checker.__file__).parent
_RESULT_FUNCS = {"ok", "warn", "err", "_ok", "_warn", "_err", "fragment"}

#: Calls whose rule is a variable, and the rules it can take. The coverage loop
#: picks one of three region rules; the loaders and the score helper report under
#: their caller's rule with a `_shared` message.
_DYNAMIC_RULES = {
    "pass": ["low-concurrency-coverage", "med-concurrency-coverage", "high-concurrency-coverage"],
    "fail": ["low-concurrency-coverage", "med-concurrency-coverage", "high-concurrency-coverage"],
}


@dataclass(frozen=True)
class _Call:
    where: str
    rule: str | None
    keys: tuple[str, ...]
    kwargs: frozenset[str] | None  # None when the call splats a dict


def _literal_keys(node: ast.expr) -> tuple[str, ...] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.IfExp):
        body, orelse = _literal_keys(node.body), _literal_keys(node.orelse)
        if body is not None and orelse is not None:
            return body + orelse
    return None


def _calls() -> list[_Call]:
    found = []
    for path in sorted(_SRC.rglob("*.py")):
        if path.name == "messages.py" or path.parent.name == "models" and path.name == "results.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _RESULT_FUNCS
                and len(node.args) >= 2
            ):
                continue
            where = f"{path.relative_to(_SRC)}:{node.lineno}"
            rule_arg = node.args[0]
            rule = rule_arg.value if isinstance(rule_arg, ast.Constant) else None
            keys = _literal_keys(node.args[1])
            splat = any(kw.arg is None for kw in node.keywords)
            if keys is None and splat:
                # The loaders' dispatcher: rule, key and values all arrive as data
                # from `_load_json`/`_load_yaml`, whose keys are `_shared` messages.
                continue
            assert keys is not None, f"{where}: message key must be a literal (or a literal choice)"
            kwargs = None if splat else frozenset(kw.arg for kw in node.keywords if kw.arg)
            found.append(_Call(where, rule, keys, kwargs))
    return found


_CALLS = _calls()
_CATALOG = messages.catalog()


def _message(rule: str, key: str) -> messages.Message | None:
    entry = _CATALOG.get(rule)
    if entry is not None and key in entry.messages:
        return entry.messages[key]
    return _CATALOG[SHARED].messages.get(key)


def _needs(message: messages.Message) -> set[str]:
    return placeholders(message.text) | (placeholders(message.fix) if message.fix else set())


@pytest.mark.unit
class TestCatalogMatchesCode:
    def test_the_scan_found_the_call_sites(self) -> None:
        assert len(_CALLS) > 200  # guards against the scan silently matching nothing

    def test_every_key_used_exists(self) -> None:
        missing = []
        for call in _CALLS:
            rules = [call.rule] if call.rule else None
            for key in call.keys:
                candidates = rules or _DYNAMIC_RULES.get(key) or [SHARED]
                for rule in candidates:
                    if _message(rule, key) is None:
                        missing.append(f"{call.where}: {rule}.{key}")
        assert not missing, "keys missing from data/messages.yaml:\n" + "\n".join(missing)

    def test_every_call_passes_exactly_its_placeholders(self) -> None:
        wrong = []
        for call in _CALLS:
            if call.kwargs is None:
                continue  # values splatted from a dict; rendering is covered by strict mode
            rules = [call.rule] if call.rule else (_DYNAMIC_RULES.get(call.keys[0]) or [SHARED])
            for rule in rules:
                needed: set[str] = set()
                for key in call.keys:
                    message = _message(rule, key)
                    if message is None:
                        continue  # reported by test_every_key_used_exists
                    branch = _needs(message)
                    if not branch <= call.kwargs:
                        wrong.append(f"{call.where}: {rule}.{key} needs {sorted(branch)}")
                    needed |= branch
                extra = call.kwargs - needed
                if extra:
                    wrong.append(f"{call.where}: {rule} is passed unused {sorted(extra)}")
        assert not wrong, "\n".join(wrong)

    def test_no_message_is_unused(self) -> None:
        used = {(call.rule, key) for call in _CALLS if call.rule for key in call.keys}
        for key, rules in _DYNAMIC_RULES.items():
            used |= {(rule, key) for rule in rules}
        unused = [
            f"{rule}.{key}"
            for rule, entry in _CATALOG.items()
            if rule != SHARED
            for key in entry.messages
            if (rule, key) not in used
        ]
        assert not unused, "messages no code produces:\n" + "\n".join(unused)


@pytest.mark.unit
class TestCatalog:
    def test_every_rule_has_a_title_and_spec(self) -> None:
        for rule, entry in _CATALOG.items():
            if rule == SHARED:
                continue
            assert entry.title and entry.title != rule, rule
            assert entry.spec.startswith("§"), f"{rule}: spec {entry.spec!r}"

    def test_specs_use_one_notation(self) -> None:
        for rule, entry in _CATALOG.items():
            for key, message in entry.messages.items():
                assert "#" not in message.spec, f"{rule}.{key}: {message.spec!r}"

    @pytest.mark.parametrize(
        ("rule", "key"),
        [(rule, key) for rule, entry in _CATALOG.items() for key in entry.messages],
    )
    def test_every_template_renders(self, rule: str, key: str) -> None:
        message = _CATALOG[rule].messages[key]
        values = dict.fromkeys(_needs(message), 1.5)
        rendered = messages.render(rule if rule != SHARED else "path-exists", key, values)
        assert rendered.text


@pytest.mark.unit
class TestRendering:
    def test_attribute_access_is_refused(self, tmp_path: Path) -> None:
        bad = tmp_path / "messages.yaml"
        bad.write_text("r:\n  title: R\n  messages:\n    k: '{a.b}'\n")
        with pytest.raises(MessageCatalogError, match="plain names"):
            messages.load(bad)

    def test_a_missing_value_raises_in_strict_mode(self) -> None:
        with pytest.raises(MessageCatalogError):
            messages.render("model-name-valid", "fail", {})

    def test_outside_strict_mode_a_bad_message_degrades(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(messages, "strict", False)
        rendered = messages.render("model-name-valid", "no-such-key", {"name": "x"})
        assert rendered.text == "model-name-valid (no-such-key): name='x'"
        assert rendered.title == "Benchmark model name"
