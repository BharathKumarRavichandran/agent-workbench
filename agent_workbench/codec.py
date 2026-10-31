"""Strict JSON decoders for catalogs and profiles."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .model import (
    Artifact,
    ArtifactKind,
    Catalog,
    ComponentKind,
    Marketplace,
    Plugin,
    PluginId,
    Profile,
    ProfileId,
    ProfileTarget,
    Provenance,
    RepoPath,
    Source,
    SourceId,
    SourceKind,
    TargetId,
)

_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    return value


def _list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{location} must be an array")
    return value


def _string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{location} must be a non-empty string")
    return value


def _keys(data: dict[str, Any], required: set[str], optional: set[str], location: str) -> None:
    missing = required - data.keys()
    unknown = data.keys() - required - optional
    if missing:
        raise ValueError(f"{location} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{location} has unknown fields: {', '.join(sorted(unknown))}")


def _identifier(value: Any, location: str) -> str:
    result = _string(value, location)
    if not _ID.fullmatch(result):
        raise ValueError(f"{location} must match {_ID.pattern}")
    return result


def load_json(path: Path) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}: {path}")
            result[key] = value
        return result

    return _object(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object), str(path))


def _schema_version(value: Any, expected: int, location: str) -> int:
    if type(value) is not int or value != expected:
        raise ValueError(f"{location} must be integer {expected}")
    return value


def _upstream(value: Any, location: str) -> str:
    result = _string(value, location)
    if result.startswith("-") or "\x00" in result:
        raise ValueError(f"{location} is not a safe upstream URL")
    parsed = urlparse(result)
    if parsed.scheme not in {"https", "ssh", "git"} and not re.fullmatch(r"[^@\s]+@[^:\s]+:.+", result):
        raise ValueError(f"{location} must be an https, ssh, git, or scp-style URL")
    return result


def _branch(value: Any, location: str) -> str:
    result = _string(value, location)
    forbidden = ("..", "@{", " ", "~", "^", ":", "?", "*", "[", "\\")
    if result.startswith("-") or result.startswith("/") or result.endswith(("/", ".")) or any(item in result for item in forbidden):
        raise ValueError(f"{location} is not a safe Git branch")
    return result


def decode_catalog(path: Path) -> Catalog:
    data = load_json(path)
    _keys(data, {"schemaVersion", "marketplace", "sources", "plugins"}, {"$schema"}, "catalog")
    _schema_version(data["schemaVersion"], 2, "catalog.schemaVersion")

    market = _object(data["marketplace"], "catalog.marketplace")
    _keys(market, {"id", "name", "owner", "description"}, set(), "catalog.marketplace")
    owner = _object(market["owner"], "catalog.marketplace.owner")
    _keys(owner, {"name"}, set(), "catalog.marketplace.owner")
    marketplace = Marketplace(
        id=_identifier(market["id"], "catalog.marketplace.id"),
        name=_string(market["name"], "catalog.marketplace.name"),
        owner_name=_string(owner["name"], "catalog.marketplace.owner.name"),
        description=_string(market["description"], "catalog.marketplace.description"),
    )

    sources: list[Source] = []
    for index, raw in enumerate(_list(data["sources"], "catalog.sources")):
        location = f"catalog.sources[{index}]"
        item = _object(raw, location)
        _keys(item, {"id", "kind", "path"}, {"upstream", "branch"}, location)
        try:
            source_kind = SourceKind(_string(item["kind"], f"{location}.kind"))
        except ValueError as error:
            raise ValueError(f"{location}.kind is unsupported: {item['kind']!r}") from error
        sources.append(
            Source(
                id=SourceId(_identifier(item["id"], f"{location}.id")),
                kind=source_kind,
                path=RepoPath(_string(item["path"], f"{location}.path")),
                upstream=_upstream(item["upstream"], f"{location}.upstream") if "upstream" in item else None,
                branch=_branch(item["branch"], f"{location}.branch") if "branch" in item else None,
            )
        )

    plugins: list[Plugin] = []
    for index, raw in enumerate(_list(data["plugins"], "catalog.plugins")):
        location = f"catalog.plugins[{index}]"
        item = _object(raw, location)
        _keys(item, {"id", "source", "artifacts"}, {"provenance"}, location)
        artifacts_raw = _object(item["artifacts"], f"{location}.artifacts")
        if not artifacts_raw:
            raise ValueError(f"{location}.artifacts must contain at least one artifact")
        artifacts: dict[ArtifactKind, Artifact] = {}
        for kind_value, artifact_raw in artifacts_raw.items():
            artifact_kind = ArtifactKind(_identifier(kind_value, f"{location}.artifacts key"))
            artifact_data = _object(artifact_raw, f"{location}.artifacts.{kind_value}")
            _keys(artifact_data, {"path"}, set(), f"{location}.artifacts.{kind_value}")
            artifacts[artifact_kind] = Artifact(
                kind=artifact_kind,
                path=RepoPath(_string(artifact_data["path"], f"{location}.artifacts.{kind_value}.path")),
            )
        provenance = None
        if "provenance" in item:
            provenance_raw = _object(item["provenance"], f"{location}.provenance")
            _keys(provenance_raw, {"upstream"}, {"notice"}, f"{location}.provenance")
            provenance = Provenance(
                upstream=_upstream(provenance_raw["upstream"], f"{location}.provenance.upstream"),
                notice=RepoPath(_string(provenance_raw["notice"], f"{location}.provenance.notice"))
                if "notice" in provenance_raw
                else None,
            )
        plugins.append(
            Plugin(
                id=PluginId(_identifier(item["id"], f"{location}.id")),
                source=SourceId(_identifier(item["source"], f"{location}.source")),
                artifacts=artifacts,
                provenance=provenance,
            )
        )

    source_map = {source.id: source for source in sources}
    for source in sources:
        if source.kind is SourceKind.CURATED_COPY and source.upstream is None:
            raise ValueError(f"curated-copy source {source.id} requires upstream provenance")
        if source.kind is SourceKind.GIT_SUBMODULE and (source.upstream is None or source.branch is None):
            raise ValueError(f"git-submodule source {source.id} requires upstream and branch")
    for plugin in plugins:
        plugin_source = source_map.get(plugin.source)
        if plugin_source is not None and plugin_source.kind is SourceKind.CURATED_COPY:
            if plugin.provenance is None or plugin.provenance.notice is None:
                raise ValueError(f"curated-copy plugin {plugin.id} requires provenance with a notice")

    return Catalog(2, marketplace, tuple(sources), tuple(plugins))


def decode_profile(path: Path) -> Profile:
    data = load_json(path)
    _keys(data, {"schemaVersion", "id", "plugins", "targets"}, {"$schema"}, "profile")
    version = data["schemaVersion"]
    if type(version) is not int or version not in (1, 2):
        raise ValueError("profile.schemaVersion must be integer 1 or 2")
    selector = _object(data["plugins"], "profile.plugins")
    _keys(selector, {"include", "exclude"}, set(), "profile.plugins")
    include = tuple(_string(value, "profile.plugins.include[]") for value in _list(selector["include"], "profile.plugins.include"))
    exclude = tuple(PluginId(_identifier(value, "profile.plugins.exclude[]")) for value in _list(selector["exclude"], "profile.plugins.exclude"))
    targets: list[ProfileTarget] = []
    for index, raw in enumerate(_list(data["targets"], "profile.targets")):
        location = f"profile.targets[{index}]"
        item = _object(raw, location)
        _keys(item, {"id"}, {"destination"} if version == 1 else {"destination", "components", "projectRoot"}, location)
        components = (ComponentKind.SKILLS,)
        if "components" in item:
            components = tuple(ComponentKind(_string(value, f"{location}.components[]")) for value in _list(item["components"], f"{location}.components"))
            if len(set(components)) != len(components):
                raise ValueError(f"{location}.components must be unique")
        project_root = _string(item["projectRoot"], f"{location}.projectRoot") if "projectRoot" in item else None
        if project_root is not None:
            try:
                expanded_project_root = Path(project_root).expanduser()
            except RuntimeError as error:
                raise ValueError(f"{location}.projectRoot has an unknown user") from error
            if "\x00" in project_root or not expanded_project_root.is_absolute() or ".." in Path(project_root).parts:
                raise ValueError(f"{location}.projectRoot must be an absolute contained path")
        targets.append(
            ProfileTarget(
                id=TargetId(_identifier(item["id"], f"{location}.id")),
                destination=_string(item["destination"], f"{location}.destination") if "destination" in item else None,
                components=components,
                project_root=project_root,
            )
        )
    if version == 2 and len({target.id for target in targets}) != len(targets):
        raise ValueError("profile target ids must be unique")
    return Profile(
        schema_version=version,
        id=ProfileId(_identifier(data["id"], "profile.id")),
        include_plugins=include,
        exclude_plugins=exclude,
        targets=tuple(targets),
    )
