"""Cross-platform path validation shared by storage schemas."""

from pathlib import PurePosixPath, PureWindowsPath


def normalize_relative_path(value: str | None, label: str) -> str | None:
    """Normalize a safe relative path to portable forward-slash form."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a relative path or null")
    candidate = value.strip()
    if not candidate:
        return None
    windows = PureWindowsPath(candidate)
    posix = PurePosixPath(candidate.replace("\\", "/"))
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise ValueError(f"{label} must be relative to its storage root")
    if any(part in {"", ".", ".."} or ":" in part for part in posix.parts):
        raise ValueError(f"{label} contains an unsafe path segment")
    return posix.as_posix()


def normalize_project_asset_path(
    value: str | None, label: str, directory: str
) -> str | None:
    normalized = normalize_relative_path(value, label)
    if normalized and PurePosixPath(normalized).parts[0] != directory:
        raise ValueError(f"{label} must be stored under {directory}/")
    return normalized

