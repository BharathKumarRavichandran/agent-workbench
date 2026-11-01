"""Inert discovery and validation for portable component artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePath

from ..inspection import discover_skills, load_frontmatter, load_json_object, validate_directory_tree
from ..model import ArtifactInventory, ArtifactKind, ComponentInfo, ResolvedPlugin, Representation
from .base import ArtifactInspector


def _safe_name(value: str, path: Path) -> str:
    if (
        not value
        or value in {".", ".."}
        or Path(value).is_absolute()
        or PurePath(value).name != value
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(f"component name must be one safe filename component: {path}: {value!r}")
    return value


@dataclass(frozen=True)
class OpaqueArtifactInspector:
    kind: ArtifactKind

    def inspect(self, plugin: ResolvedPlugin) -> ArtifactInventory:
        root = plugin.artifact_paths[self.kind]
        if root.is_symlink():
            raise ValueError(f"artifact root may not be a symlink: {root}")
        if not root.exists():
            raise ValueError(f"artifact path does not exist: {root}")
        return ArtifactInventory(plugin.plugin.id, self.kind, root, ())


@dataclass(frozen=True)
class SkillsInspector:
    kind: ArtifactKind = ArtifactKind("agent-skills")

    def inspect(self, plugin: ResolvedPlugin) -> ArtifactInventory:
        root = plugin.artifact_paths[self.kind]
        components = tuple(
            ComponentInfo(skill.name, skill.root.relative_to(root).as_posix())
            for skill in discover_skills(root)
        )
        return ArtifactInventory(plugin.plugin.id, self.kind, root, components)


@dataclass(frozen=True)
class DocumentDirectoryInspector:
    kind: ArtifactKind
    suffix: str
    require_name: bool = False
    require_description: bool = False

    def inspect(self, plugin: ResolvedPlugin) -> ArtifactInventory:
        root = plugin.artifact_paths[self.kind]
        validate_directory_tree(root)
        components: list[ComponentInfo] = []
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix != self.suffix:
                continue
            values = load_frontmatter(root, path)
            if self.require_name:
                name = _safe_name(values.get("name", ""), path)
                if name != path.stem:
                    raise ValueError(f"component name must match filename stem: {path}: {name!r}")
            else:
                name = _safe_name(path.stem, path)
            if self.require_description and not values.get("description"):
                raise ValueError(f"component frontmatter requires description: {path}")
            components.append(ComponentInfo(name, path.relative_to(root).as_posix()))
        return ArtifactInventory(plugin.plugin.id, self.kind, root, tuple(components))


def classify_hooks(data: dict, alias: str = "hooks") -> Representation:
    hooks = data.get('hooks')
    if not isinstance(hooks, dict):
        raise ValueError('hooks.json requires a hooks object')
    shapes = set()
    for event, entries in hooks.items():
        if not event or not isinstance(entries, list):
            raise ValueError('hook events must map to arrays')
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('hook entries must be objects')
            if 'hooks' in entry and 'command' not in entry:
                if not isinstance(entry['hooks'], list) or ('matcher' in entry and not isinstance(entry['matcher'], str)):
                    raise ValueError('Claude matcher groups require a hooks array and optional string matcher')
                for handler in entry['hooks']:
                    if not isinstance(handler, dict) or not isinstance(handler.get('type'), str):
                        raise ValueError('Claude handlers require type')
                    if handler['type'] == 'command' and (not isinstance(handler.get('command'), str) or not handler['command']):
                        raise ValueError('Claude command handler requires command')
                shapes.add(Representation.CLAUDE_HOOK_BUNDLE)
            elif 'command' in entry and 'hooks' not in entry:
                if not isinstance(entry['command'], str) or not entry['command']:
                    raise ValueError('Cursor hook requires command')
                shapes.add(Representation.HOOK_BUNDLE)
            else:
                raise ValueError('ambiguous hook entry representation')
    expected = {'cursor-hooks': Representation.HOOK_BUNDLE,
                'claude-hooks': Representation.CLAUDE_HOOK_BUNDLE}.get(alias)
    if not shapes:
        if expected is None and 'version' not in data:
            raise ValueError('empty hooks require an explicit representation alias or Cursor version')
        shapes.add(expected or Representation.HOOK_BUNDLE)
    if len(shapes) != 1 or (expected is not None and expected not in shapes):
        raise ValueError('mixed or incompatible hook representations')
    representation = next(iter(shapes))
    if representation is Representation.CLAUDE_HOOK_BUNDLE and 'version' in data:
        raise ValueError('Claude hooks do not have a Cursor version field')
    if representation is Representation.HOOK_BUNDLE and (type(data.get('version', 1)) is not int or data.get('version', 1) != 1):
        raise ValueError('Cursor hooks require version 1')
    return representation


@dataclass(frozen=True)
class HooksInspector:
    kind: ArtifactKind = ArtifactKind("hooks")

    def inspect(self, plugin: ResolvedPlugin) -> ArtifactInventory:
        root = plugin.artifact_paths[self.kind]
        validate_directory_tree(root)
        data = load_json_object(root, root / "hooks.json")
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            raise ValueError(f"hooks.json requires a hooks object: {root / 'hooks.json'}")
        representation = classify_hooks(data, str(self.kind))
        components: list[ComponentInfo] = []
        for event, entries in sorted(hooks.items()):
            if not isinstance(event, str) or not isinstance(entries, list):
                raise ValueError(f"hook events must map to arrays: {root / 'hooks.json'}")
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise ValueError(f"hook entries must be objects: {root / 'hooks.json'}")
                components.append(ComponentInfo(f"{event}:{index}", "hooks.json"))
        return ArtifactInventory(plugin.plugin.id, self.kind, root, tuple(components), representation)


@dataclass(frozen=True)
class McpInspector:
    kind: ArtifactKind = ArtifactKind("mcp")

    def inspect(self, plugin: ResolvedPlugin) -> ArtifactInventory:
        root = plugin.artifact_paths[self.kind]
        data = load_json_object(root.parent, root)
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            raise ValueError(f"MCP document requires an mcpServers object: {root}")
        components = tuple(
            ComponentInfo(_safe_name(name, root), root.name)
            for name, value in sorted(servers.items())
            if isinstance(name, str) and isinstance(value, dict)
        )
        if len(components) != len(servers):
            raise ValueError(f"MCP servers must map names to objects: {root}")
        return ArtifactInventory(plugin.plugin.id, self.kind, root, components)


def built_in_component_inspectors() -> tuple[ArtifactInspector, ...]:
    return (
        SkillsInspector(),
        DocumentDirectoryInspector(ArtifactKind("agents"), ".md", require_name=True, require_description=True),
        DocumentDirectoryInspector(ArtifactKind("rules"), ".mdc", require_description=True),
        HooksInspector(),
        HooksInspector(ArtifactKind("cursor-hooks")),
        HooksInspector(ArtifactKind("claude-hooks")),
        McpInspector(),
        DocumentDirectoryInspector(ArtifactKind("commands"), ".md"),
    )
