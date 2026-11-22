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
        """Locate parsed source Markdown.

        Returns
        -------
        Path
            Vault raw/ directory.
        """
        return self.data_dir / "raw"

    @property
    def drops_dir(self) -> Path:
        """Locate imported files awaiting ingestion.

        Returns
        -------
        Path
            Vault drops/ directory.
        """
        return self.data_dir / "drops"

    @property
    def wiki_dir(self) -> Path:
        """Locate compiled wiki content.

        Returns
        -------
        Path
            Vault wiki/ directory.
        """
        return self.data_dir / "wiki"

    @property
    def backend_dir(self) -> Path:
        """Locate private backend runtime files.

        Returns
        -------
        Path
            Vault .backend/ directory.
        """
        return self.data_dir / ".backend"

    @property
    def search_db_path(self) -> Path:
        """Locate the derived search database.

        Returns
        -------
        Path
            Vault search.db path.
        """
        return self.data_dir / "search.db"


def get_vault_paths() -> VaultPaths:
    """Derive storage paths from settings.VAULT_DIR.

    Returns
    -------
    VaultPaths
        Storage paths derived from Django's active VAULT_DIR.
    """
    return VaultPaths(Path(settings.VAULT_DIR))
