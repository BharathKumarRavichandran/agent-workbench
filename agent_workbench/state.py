"""Machine-local installation ownership state."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class StateEntry:
    key: str
    plugin: str
    source: str
    destination: str

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "plugin": self.plugin,
            "source": self.source,
            "destination": self.destination,
        }


@dataclass(frozen=True)
class InstallState:
    schema_version: int
    repository: str
    marketplace: str
    profile: str
    target: str
    destination_root: str
    entries: Mapping[str, StateEntry]

    def as_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "repository": self.repository,
            "marketplace": self.marketplace,
            "profile": self.profile,
            "target": self.target,
            "destinationRoot": self.destination_root,
            "entries": {key: entry.as_dict() for key, entry in sorted(self.entries.items())},
        }


def default_state_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return Path(base).expanduser() if base else Path.home() / ".local" / "state"


def state_path(root: Path, marketplace: str, profile: str, target: str) -> Path:
    return root / "agent-workbench" / marketplace / profile / f"{target}.json"


def _object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{location} must be a non-empty string")
    return value


def _keys(data: Mapping[str, Any], required: set[str], location: str) -> None:
    missing = required - data.keys()
    unknown = data.keys() - required
    if missing:
        raise ValueError(f"{location} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{location} has unknown fields: {', '.join(sorted(unknown))}")


def _identifier(value: Any, location: str) -> str:
    result = _string(value, location)
    if not _IDENTIFIER.fullmatch(result):
        raise ValueError(f"{location} is invalid")
    return result


def _absolute_path(value: Any, location: str) -> str:
    result = _string(value, location)
    if not Path(result).is_absolute():
        raise ValueError(f"{location} must be absolute")
    return result


def _source_path(value: Any, location: str) -> str:
    result = _string(value, location)
    path = PurePosixPath(result)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts) or "\\" in result:
        raise ValueError(f"{location} must be a contained repository source path")
    return result


def _entry_key(value: Any, location: str) -> str:
    result = _string(value, location)
    prefix, separator, name = result.partition(":")
    if prefix != "skill" or not separator or name in {".", ".."} or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError(f"{location} is not a valid entry key")
    return result


def load_state(path: Path) -> InstallState | None:
    if not path.is_file():
        return None
    data = _object(
        json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys),
        str(path),
    )
    required = {"schemaVersion", "repository", "marketplace", "profile", "target", "destinationRoot", "entries"}
    _keys(data, required, "installation state")
    if data["schemaVersion"] != 2:
        raise ValueError(f"unsupported installation state: {path}")
    raw_entries = _object(data["entries"], "installation state.entries")
    entries: dict[str, StateEntry] = {}
    for raw_key, raw_value in raw_entries.items():
        key = _entry_key(raw_key, "installation state entry key")
        value = _object(raw_value, f"installation state.entries[{key!r}]")
        _keys(value, {"key", "plugin", "source", "destination"}, f"installation state.entries[{key!r}]")
        embedded_key = _entry_key(value["key"], f"installation state.entries[{key!r}].key")
        if embedded_key != key:
            raise ValueError(f"installation state entry key does not match mapping key: {key!r}")
        entries[key] = StateEntry(
            key=embedded_key,
            plugin=_identifier(value["plugin"], f"installation state.entries[{key!r}].plugin"),
            source=_source_path(value["source"], f"installation state.entries[{key!r}].source"),
            destination=_absolute_path(value["destination"], f"installation state.entries[{key!r}].destination"),
        )
    return InstallState(
        schema_version=2,
        repository=_absolute_path(data["repository"], "installation state.repository"),
        marketplace=_identifier(data["marketplace"], "installation state.marketplace"),
        profile=_identifier(data["profile"], "installation state.profile"),
        target=_identifier(data["target"], "installation state.target"),
        destination_root=_absolute_path(data["destinationRoot"], "installation state.destinationRoot"),
        entries=entries,
    )


def write_state(path: Path, state: InstallState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(state.as_dict(), indent=2, ensure_ascii=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def remove_state(path: Path) -> None:
    if path.exists():
        path.unlink()
