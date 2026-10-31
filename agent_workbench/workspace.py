"""Load one repository checkout into typed domain objects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .adapters.registry import AdapterRegistry, build_registry
from .codec import decode_catalog, decode_profile
from .model import Catalog, Profile, ResolvedProfile
from .profiles import resolve_profile

_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Workspace:
    root: Path
    catalog: Catalog
    profile: Profile
    resolved: ResolvedProfile
    registry: AdapterRegistry

    @classmethod
    def load(
        cls,
        root: Path,
        profile_id: str = "default",
        registry: AdapterRegistry | None = None,
    ) -> "Workspace":
        repository = root.resolve()
        if not _PROFILE_ID.fullmatch(profile_id):
            raise ValueError(f"profile id is invalid: {profile_id!r}")
        profiles_root = repository / "profiles"
        profile_path = (profiles_root / f"{profile_id}.json").resolve()
        if profile_path.parent != profiles_root.resolve():
            raise ValueError(f"profile id escapes profiles directory: {profile_id!r}")
        catalog = decode_catalog(repository / "catalog" / "plugins.json")
        profile = decode_profile(profile_path)
        if str(profile.id) != profile_id:
            raise ValueError(f"profile id {profile.id!r} does not match requested profile {profile_id!r}")
        active_registry = registry or build_registry()
        resolved = resolve_profile(repository, catalog, profile)
        return cls(repository, catalog, profile, resolved, active_registry)
