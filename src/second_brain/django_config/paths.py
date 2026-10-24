"""Startup-owned vault locations. Preferences never select the running vault."""

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


@dataclass(frozen=True)
class VaultPaths:
    """Locations derived from the single vault selected by the launcher."""

    data_dir: Path

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def drops_dir(self) -> Path:
        return self.data_dir / "drops"

    @property
    def wiki_dir(self) -> Path:
        return self.data_dir / "wiki"

    @property
    def backend_dir(self) -> Path:
        return self.data_dir / ".backend"

    @property
    def search_db_path(self) -> Path:
        return self.data_dir / "search.db"


def get_vault_paths() -> VaultPaths:
    """Derive storage paths from settings.VAULT_DIR."""
    return VaultPaths(Path(settings.VAULT_DIR))
