"""Machine-local installation ownership state."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .model import ComponentKind, DirectorySymlink, FileCopy, DirectoryCopy, PathResource

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class StateEntry:
    key: str
    plugin: str
    source: str
    destination: str
    component: ComponentKind
    resource: PathResource
    source_repository: str

    trust: str | None = None
    trust_proof: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            **({"trustProof": self.trust_proof} if self.trust_proof is not None else {}),
            "key": self.key,
            "plugin": self.plugin,
            "source": self.source,
            "destination": self.destination,
            "component": self.component.value,
            "resourceKind": self.resource.kind,
            "sourceRepository": self.source_repository,
            **({"trust": self.trust} if isinstance(self.resource, DirectoryCopy) else {}),
            **({"digest": self.resource.digest} if isinstance(self.resource, (FileCopy, DirectoryCopy)) else {}),
        }


@dataclass(frozen=True)
class SlotState:
    key: str
    plugin: str
    component: ComponentKind
    document: str
    slot: str
    digest: str
    trust: str
    trust_proof: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {**({"trustProof": self.trust_proof} if self.trust_proof is not None else {}),
                "key": self.key, "plugin": self.plugin, "component": self.component.value,
                "document": self.document, "slot": self.slot, "digest": self.digest, "trust": self.trust}


@dataclass(frozen=True)
class InstallState:
    schema_version: int
    repository: str
    marketplace: str
    profile: str
    target: str
    destination_root: str
    entries: Mapping[str, StateEntry]
    destination_roots: Mapping[ComponentKind, str] = field(default_factory=dict)

    slots: Mapping[str, SlotState] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        entries = {key: entry.as_dict() for key, entry in sorted(self.entries.items())}
        if self.schema_version == 2:
            entries = {key: {name: value for name, value in entry.items()
                            if name in {"key", "plugin", "source", "destination"}}
                       for key, entry in entries.items()}
        return {
            "schemaVersion": self.schema_version,
            "repository": self.repository,
            "marketplace": self.marketplace,
            "profile": self.profile,
            "target": self.target,
            "destinationRoot": self.destination_root,
            "entries": entries,
            **({"slots": {key: slot.as_dict() for key, slot in sorted(self.slots.items())}}
               if self.schema_version == 4 else {}),
            **({"destinationRoots": {key.value: value for key, value in sorted(self.destination_roots.items())}}
               if self.schema_version >= 3 else {}),
        }


def default_state_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return Path(base).expanduser() if base else Path.home() / ".local" / "state"


def state_path(root: Path, marketplace: str, profile: str, target: str) -> Path:
    return root.expanduser().resolve() / "agent-workbench" / marketplace / profile / f"{target}.json"


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
    if not Path(result).is_absolute() or ".." in Path(result).parts:
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
    if prefix not in {"skill", "agent", "rule", "command", "hook"} or not separator or not name or name in {".", ".."} or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError(f"{location} is not a valid entry key")
    return result


def _trust(value: Any) -> str:
    token = _string(value, "trust grant")
    if not re.fullmatch(r"aw-trust-v1:[0-9a-f]{64}", token):
        raise ValueError("invalid trust grant")
    return token


def _proof(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"aw-proof-v1:[0-9a-f]{64}", value):
        raise ValueError("invalid trust proof")
    return value


def load_state(path: Path) -> InstallState | None:
    if not path.is_file():
        return None
    data = _object(
        json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys),
        str(path),
    )
    required = {"schemaVersion", "repository", "marketplace", "profile", "target", "destinationRoot", "entries"}
    version = data.get("schemaVersion")
    if type(version) is not int or version not in (2, 3, 4):
        raise ValueError(f"unsupported installation state: {path}")
    _keys(data, required | ({"destinationRoots"} if version >= 3 else set()) | ({"slots"} if version == 4 else set()), "installation state")
    raw_entries = _object(data["entries"], "installation state.entries")
    entries: dict[str, StateEntry] = {}
    for raw_key, raw_value in raw_entries.items():
        key = _entry_key(raw_key, "installation state entry key")
        value = _object(raw_value, f"installation state.entries[{key!r}]")
        resource: PathResource = DirectorySymlink()
        component = ComponentKind.SKILLS
        fields = {"key", "plugin", "source", "destination"}
        if version >= 3:
            component = ComponentKind(value.get("component"))
            kind = value.get("resourceKind")
            fields |= {"component", "resourceKind", "sourceRepository"}
            if kind == "file-copy" or (kind == "directory-copy" and version == 4):
                fields.add("digest")
                digest = _string(value.get("digest"), "installation state entry digest")
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError("installation state entry digest must be SHA-256")
                resource = DirectoryCopy(digest) if kind == "directory-copy" else FileCopy(digest)
                if kind == "directory-copy":
                    fields.add("trust")
                    _trust(value.get("trust"))
            elif kind != "directory-symlink":
                raise ValueError("installation state entry resourceKind is unsupported")
        if "trustProof" in value and isinstance(resource, DirectoryCopy):
            fields.add("trustProof")
            _proof(value["trustProof"])
        _keys(value, fields, f"installation state.entries[{key!r}]")
        prefix = {ComponentKind.SKILLS: "skill", ComponentKind.AGENTS: "agent",
                  ComponentKind.RULES: "rule", ComponentKind.COMMANDS: "command", ComponentKind.HOOKS: "hook"}.get(component)
        if key.partition(":")[0] != prefix or (isinstance(resource, DirectorySymlink) != (component is ComponentKind.SKILLS)):
            raise ValueError("installation state entry key, component and resource kind disagree")
        if (component is ComponentKind.HOOKS) != isinstance(resource, DirectoryCopy):
            raise ValueError("hook bundles require directory-copy")
        embedded_key = _entry_key(value["key"], f"installation state.entries[{key!r}].key")
        if embedded_key != key:
            raise ValueError(f"installation state entry key does not match mapping key: {key!r}")
        entries[key] = StateEntry(
            trust=value.get("trust"),
            trust_proof=value.get("trustProof"),
            key=embedded_key,
            plugin=_identifier(value["plugin"], f"installation state.entries[{key!r}].plugin"),
            source=_source_path(value["source"], f"installation state.entries[{key!r}].source"),
            destination=_absolute_path(value["destination"], f"installation state.entries[{key!r}].destination"),
            component=component,
            resource=resource,
            source_repository=_absolute_path(value["sourceRepository"] if version >= 3 else data["repository"],
                                             f"installation state.entries[{key!r}].sourceRepository"),
        )
    roots = ({ComponentKind(key): _absolute_path(value, "installation state.destinationRoots")
              for key, value in _object(data["destinationRoots"], "installation state.destinationRoots").items()}
             if version >= 3 else {ComponentKind.SKILLS: data["destinationRoot"]})
    if roots.get(ComponentKind.SKILLS) != data["destinationRoot"]:
        raise ValueError("installation state destinationRoots skills must match destinationRoot")
    if any(entry.component not in roots for entry in entries.values()):
        raise ValueError("installation state entry lacks a destination root")
    destinations: set[Path] = set()
    for key, entry in entries.items():
        destination = Path(entry.destination)
        if destination.name != key.partition(":")[2]:
            raise ValueError(f"installation state entry key does not match destination: {key!r}")
        if destination.parent != Path(roots[entry.component]):
            raise ValueError(f"state destination must be a direct child of installation root: {destination}")
        if destination in destinations:
            raise ValueError("installation state destinations must be unique")
        destinations.add(destination)
    slots: dict[str, SlotState] = {}
    if version == 4:
        for key, raw in _object(data["slots"], "installation state.slots").items():
            value = _object(raw, "slot")
            if "trustProof" in value:
                _proof(value["trustProof"])
            _keys(value, {"key", "plugin", "component", "document", "slot", "digest", "trust"} | ({"trustProof"} if "trustProof" in value else set()), "slot")
            component = ComponentKind(value["component"])
            if component not in {ComponentKind.HOOKS, ComponentKind.MCP} or key != value["key"]:
                raise ValueError("invalid shared slot identity")
            plugin = _identifier(value["plugin"], "slot plugin")
            slot = _string(value["slot"], "slot name")
            if key != f"{component.value}:{plugin}:{slot}":
                raise ValueError("invalid shared slot key")
            digest = _string(value["digest"], "slot digest")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid shared slot digest")
            slots[key] = SlotState(key, plugin, component,
                _absolute_path(value["document"], "slot document"), slot, digest, _trust(value["trust"]), value.get("trustProof"))
    return InstallState(
        slots=slots,
        schema_version=version,
        repository=_absolute_path(data["repository"], "installation state.repository"),
        marketplace=_identifier(data["marketplace"], "installation state.marketplace"),
        profile=_identifier(data["profile"], "installation state.profile"),
        target=_identifier(data["target"], "installation state.target"),
        destination_root=_absolute_path(data["destinationRoot"], "installation state.destinationRoot"),
        entries=entries,
        destination_roots=roots,
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
        _sync_parent(path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def remove_state(path: Path) -> None:
    if path.exists():
        path.unlink()
        _sync_parent(path)


def _sync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
