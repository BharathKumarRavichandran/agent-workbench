"""Content-bound, repeatable grants; generating a token never executes payloads."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .model import ResolvedPlugin, ComponentKind, ArtifactKind


def grant_token(plugin: ResolvedPlugin, component: ComponentKind, target: str,
                document: Path, digest: str, repository: Path) -> str:
    provenance = [str(repository), str(plugin.source.id), plugin.source.kind.value,
                  plugin.source.path.value, plugin.source.upstream, plugin.source.branch,
                  str(plugin.artifact_paths.get(ArtifactKind(component.value))),
                  plugin.plugin.provenance.upstream if plugin.plugin.provenance else None,
                  plugin.plugin.provenance.notice.value if plugin.plugin.provenance and plugin.plugin.provenance.notice else None]
    value = [str(plugin.plugin.id), component.value, target, str(document), provenance, digest]
    return 'aw-trust-v1:' + hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()
