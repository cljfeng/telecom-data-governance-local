from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    workspace_dir: Path
    data_dir: Path
    database_path: Path
    export_dir: Path
    static_dir: Path

    @classmethod
    def for_workspace(cls, workspace_dir: Path) -> "AppConfig":
        root = workspace_dir.resolve()
        package_dir = Path(__file__).resolve().parent
        data_dir = root / "data"
        return cls(
            workspace_dir=root,
            data_dir=data_dir,
            database_path=data_dir / "governance.sqlite3",
            export_dir=root / "exports",
            static_dir=package_dir / "static",
        )
