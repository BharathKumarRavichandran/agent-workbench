"""Content-bound, repeatable grants; generating a token never executes payloads."""
from __future__ import annotations
from dataclasses import replace
import hmac
import hashlib
from typing import TypeVar
import json
from pathlib import Path
from .model import ResolvedPlugin, ComponentKind, ArtifactKind
from .state import StateEntry, SlotState

TrustedRecord = TypeVar("TrustedRecord", StateEntry, SlotState)


def grant_token(plugin: ResolvedPlugin, component: ComponentKind, target: str,
                document: Path, digest: str, repository: Path) -> str:
    provenance = [str(repository), str(plugin.source.id), plugin.source.kind.value,
                  plugin.source.path.value, plugin.source.upstream, plugin.source.branch,
                  str(plugin.artifact_paths.get(ArtifactKind(component.value))),
                  plugin.plugin.provenance.upstream if plugin.plugin.provenance else None,
                  plugin.plugin.provenance.notice.value if plugin.plugin.provenance and plugin.plugin.provenance.notice else None]
    value = [str(plugin.plugin.id), component.value, target, str(document), provenance, digest]
    return 'aw-trust-v1:' + hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def record_proof(record: StateEntry | SlotState, file: Path, create: bool = False) -> str:
    from .transactions import _journal_key, _canonical
    value = record.as_dict()
    value.pop('trustProof', None)
    message = ['aw-persisted-trust-v1', str(file), value]
    return 'aw-proof-v1:' + hmac.new(_journal_key(file, create), _canonical(message), hashlib.sha256).hexdigest()


def authenticated(record: StateEntry | SlotState | None, file: Path) -> bool:
    if record is None or record.trust_proof is None:
        return False
    try:
        return hmac.compare_digest(record.trust_proof, record_proof(record, file))
    except (OSError, ValueError):
        return False


def reuse_proof(record: TrustedRecord, prior: TrustedRecord | None, file: Path) -> TrustedRecord:
    if authenticated(prior, file):
        candidate = replace(record, trust_proof=prior.trust_proof)
        if candidate == prior:
            return candidate
    return record
