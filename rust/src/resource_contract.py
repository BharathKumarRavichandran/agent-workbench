import json
from pathlib import Path

from agent_workbench.installation import InstallOperation, OperationKind
from agent_workbench.model import ComponentKind, DirectoryCopy, DirectorySymlink, FileCopy
from agent_workbench.state import StateEntry

DIGEST = "ab" * 32
PROOF = "aw-proof-v1:" + "cd" * 32
TRUST = "aw-trust-v1:" + "ef" * 32


def operation(component, resource, source):
    return InstallOperation(
        OperationKind.CREATE,
        "sample",
        Path("/dest/sample"),
        source,
        "destination is absent",
        component=component,
        resource=resource,
    ).as_dict()


def entry(key, component, resource, trust=None, proof=None):
    return StateEntry(
        key,
        "pstack",
        "plugins/sample",
        "/dest/sample",
        component,
        resource,
        "/repo",
        trust,
        proof,
    ).as_dict()


def pretty(value):
    return json.dumps(value, indent=2, ensure_ascii=False)


cases = {
    "symlink-operation": pretty(
        operation(ComponentKind.SKILLS, DirectorySymlink(), Path("/src/skill"))
    ),
    "file-operation": pretty(
        operation(ComponentKind.AGENTS, FileCopy(DIGEST), Path("/src/agent.md"))
    ),
    "directory-operation": pretty(
        operation(ComponentKind.HOOKS, DirectoryCopy(DIGEST), Path("/src/hooks"))
    ),
    "symlink-entry": pretty(entry("skill:sample", ComponentKind.SKILLS, DirectorySymlink())),
    "file-entry": pretty(
        entry("agent:sample.md", ComponentKind.AGENTS, FileCopy(DIGEST))
    ),
    "directory-entry": pretty(
        entry("hook:sample", ComponentKind.HOOKS, DirectoryCopy(DIGEST), TRUST)
    ),
    "directory-entry-proof": pretty(
        entry("hook:sample", ComponentKind.HOOKS, DirectoryCopy(DIGEST), TRUST, PROOF)
    ),
    "directory-entry-null-trust": pretty(
        entry("hook:sample", ComponentKind.HOOKS, DirectoryCopy(DIGEST))
    ),
}
print(json.dumps(cases))
