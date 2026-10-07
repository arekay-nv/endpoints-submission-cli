"""The message catalog: the wording of every check result, kept as data.

Rules decide *whether* a submission complies; the catalog decides *what the
submitter is told*. Each result names a rule and a message key and passes the values
the message needs; ``data/messages.yaml`` supplies the text, an optional fix, the
rule's title and its spec section::

    accuracy-coverage:
      title: Accuracy at the mandatory points
      spec: "§5.3"
      messages:
        fail:
          text: "No accuracy results in {bands}."
          fix: "Run accuracy at one point in each mandatory band."

Severity stays in code, beside the rule: it is §9.1's failure action and decides
the verdict, so no wording change can make a failing submission pass.

The catalog ships inside the package and changes only with a release. What a
submitter is told is part of the checker's behaviour, so it is versioned with it
rather than overridable at run time.

Templates use :meth:`str.format` syntax restricted to plain names: ``{model}``,
``{value!r}`` and ``{ratio:.2f}`` work, ``{point.name}`` and ``{scores[0]}`` do not.
The code formats anything richer before passing it in.
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "MessageCatalogError",
    "Rendered",
    "catalog",
    "fragment",
    "placeholders",
    "render",
    "title",
]

_BUNDLED = Path(__file__).parent / "data" / "messages.yaml"

#: Section of the catalog holding messages several rules share, such as the
#: field-level errors every file loader reports under its own rule.
SHARED = "_shared"

#: When True, a missing key, a missing parameter or a template error raises
#: instead of degrading to a generic message. The test suite turns this on, so a
#: message that cannot render is a failing test rather than a quiet fallback.
strict = False


class MessageCatalogError(RuntimeError):
    """The catalog is malformed, or a result names a message it does not hold."""


@dataclass(frozen=True)
class Message:
    """One catalog entry: its template, optional fix, and spec section."""

    text: str
    fix: str | None
    spec: str


@dataclass(frozen=True)
class Rule:
    """A rule's title, default spec section, and messages by key."""

    title: str
    spec: str
    messages: dict[str, Message]


@dataclass(frozen=True)
class Rendered:
    """A message with its values filled in."""

    text: str
    fix: str | None
    spec: str
    title: str


class _PlainNameFormatter(string.Formatter):
    """:class:`string.Formatter` that only resolves plain names from the kwargs."""

    def get_field(self, field_name: str, args: Any, kwargs: Any) -> tuple[Any, str]:
        if not field_name.isidentifier():
            raise MessageCatalogError(f"placeholder {{{field_name}}} must be a plain name")
        return kwargs[field_name], field_name


_FORMATTER = _PlainNameFormatter()


def placeholders(template: str) -> set[str]:
    """The names a template needs filled in."""
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def _check_template(where: str, template: object) -> str:
    if not isinstance(template, str) or not template.strip():
        raise MessageCatalogError(f"{where}: expected a non-empty string")
    try:
        names = placeholders(template)
    except ValueError as exc:
        raise MessageCatalogError(f"{where}: {exc}") from exc
    bad = sorted(name for name in names if not name.isidentifier())
    if bad:
        raise MessageCatalogError(f"{where}: placeholders must be plain names, not {bad}")
    return template


def _parse_message(where: str, raw: object, rule_spec: str) -> Message:
    if isinstance(raw, str):
        return Message(text=_check_template(where, raw), fix=None, spec=rule_spec)
    if not isinstance(raw, Mapping):
        raise MessageCatalogError(f"{where}: expected a string or a mapping")
    unknown = set(raw) - {"text", "fix", "spec"}
    if unknown:
        raise MessageCatalogError(f"{where}: unknown keys {sorted(unknown)}")
    fix = raw.get("fix")
    return Message(
        text=_check_template(f"{where}.text", raw.get("text")),
        fix=_check_template(f"{where}.fix", fix) if fix is not None else None,
        spec=str(raw.get("spec", rule_spec)),
    )


def _parse_rule(rule_id: str, raw: object) -> Rule:
    if not isinstance(raw, Mapping):
        raise MessageCatalogError(f"{rule_id}: expected a mapping")
    unknown = set(raw) - {"title", "spec", "messages"}
    if unknown:
        raise MessageCatalogError(f"{rule_id}: unknown keys {sorted(unknown)}")
    messages = raw.get("messages")
    if not isinstance(messages, Mapping) or not messages:
        raise MessageCatalogError(f"{rule_id}: needs a non-empty `messages` mapping")
    title_ = raw.get("title", rule_id)
    spec = str(raw.get("spec", ""))
    return Rule(
        title=str(title_),
        spec=spec,
        messages={
            str(key): _parse_message(f"{rule_id}.{key}", value, spec)
            for key, value in messages.items()
        },
    )


def load(path: Path) -> dict[str, Rule]:
    """Parse and validate a catalog file."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MessageCatalogError(f"cannot read message catalog {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise MessageCatalogError(f"{path}: expected a mapping of rule ids")
    return {str(rule_id): _parse_rule(str(rule_id), raw) for rule_id, raw in data.items()}


@cache
def catalog() -> dict[str, Rule]:
    """The bundled catalog, loaded once."""
    return load(_BUNDLED)


def _lookup(rule: str, key: str) -> tuple[Rule | None, Message | None]:
    rules = catalog()
    entry = rules.get(rule)
    if entry is not None and key in entry.messages:
        return entry, entry.messages[key]
    shared = rules.get(SHARED)
    if shared is not None and key in shared.messages:
        return entry, shared.messages[key]
    return entry, None


def title(rule: str) -> str:
    """A rule's human-readable title, or its id when the catalog has none."""
    entry = catalog().get(rule)
    return entry.title if entry is not None else rule


def render(rule: str, key: str, params: Mapping[str, object]) -> Rendered:
    """Fill in message *key* of *rule* with *params*.

    Outside :data:`strict` mode a message that cannot be rendered degrades to one
    naming the rule, key and values, so a catalog mistake never stops a check.
    """
    entry, message = _lookup(rule, key)
    rule_title = entry.title if entry is not None else rule
    try:
        if message is None:
            raise MessageCatalogError(f"no message {key!r} for rule {rule!r}")
        text = _FORMATTER.vformat(message.text, (), dict(params))
        fix = _FORMATTER.vformat(message.fix, (), dict(params)) if message.fix else None
        spec = message.spec if message.spec else (entry.spec if entry is not None else "")
        return Rendered(text=text, fix=fix, spec=spec, title=rule_title)
    except (MessageCatalogError, KeyError, ValueError, IndexError) as exc:
        if strict:
            raise MessageCatalogError(f"{rule}.{key}: {exc!r}") from exc
        values = ", ".join(f"{name}={value!r}" for name, value in params.items())
        return Rendered(
            text=f"{rule} ({key}){': ' + values if values else ''}",
            fix=None,
            spec=entry.spec if entry is not None else "",
            title=rule_title,
        )


def fragment(rule: str, key: str, /, **params: object) -> str:
    """One catalog message as plain text, for findings assembled from several parts.

    Some rules collect every problem they find and report them together; each part
    is a catalog message of its own, rendered here and joined by the caller.
    """
    return render(rule, key, params).text
