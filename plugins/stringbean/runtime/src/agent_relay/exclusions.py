from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import os
from pathlib import Path
from typing import Iterable, Sequence


EXCLUSION_FILE_NAME = ".stringbeanignore"
CONTROL_DIRECTORY_NAMES = frozenset({".git", ".hg", ".svn"})

# These patterns protect conventional credential material without hiding broad
# source-code areas such as ``auth/``. Projects can add their own trust
# boundaries in ``.stringbeanignore`` or ``repository.excluded_paths``.
DEFAULT_EXCLUDED_PATHS: tuple[str, ...] = (
    ".stringbean/runs",
    ".stringbean/runs/**",
    "**/.stringbean/runs",
    "**/.stringbean/runs/**",
    ".env*",
    "**/.env*",
    "!.env.example",
    "!**/.env.example",
    "!.env.sample",
    "!**/.env.sample",
    "!.env.template",
    "!**/.env.template",
    ".secrets",
    ".secrets/**",
    "**/.secrets",
    "**/.secrets/**",
    "secrets",
    "secrets/**",
    "**/secrets",
    "**/secrets/**",
    "credentials",
    "credentials/**",
    "**/credentials",
    "**/credentials/**",
    "credentials.json",
    "**/credentials.json",
    "service-account*.json",
    "**/service-account*.json",
    "*.pem",
    "**/*.pem",
    "*.key",
    "**/*.key",
    "*.p12",
    "**/*.p12",
    "*.pfx",
    "**/*.pfx",
)

_MAX_IGNORE_FILE_BYTES = 64 * 1024
_MAX_DISCOVERY_DIRECTORIES = 20_000


def _normalize_relative_path(path: str | Path) -> str:
    value = Path(path).as_posix() if isinstance(path, Path) else str(path).replace(os.sep, "/")
    while value.startswith("./"):
        value = value[2:]
    return value.strip("/")


def _pattern_matches(path: str, raw_pattern: str) -> bool:
    pattern = raw_pattern.strip()
    if pattern.startswith("!"):
        pattern = pattern[1:]
    if not pattern:
        return False

    anchored = pattern.startswith("/")
    pattern = pattern.lstrip("/").rstrip("/")
    if not pattern:
        return False

    normalized = _normalize_relative_path(path)
    if not normalized:
        return False

    # Matching prefixes means excluding a directory also excludes everything
    # below it. Patterns without a slash follow gitignore's basename behavior.
    parts = normalized.split("/")
    prefixes = ["/".join(parts[:idx]) for idx in range(1, len(parts) + 1)]
    directory_pattern = pattern[:-3].rstrip("/") if pattern.endswith("/**") else ""
    if "/" not in pattern:
        return any(fnmatchcase(prefix.rsplit("/", 1)[-1], pattern) for prefix in prefixes)
    if anchored:
        return any(
            fnmatchcase(prefix, pattern)
            or bool(directory_pattern and fnmatchcase(prefix, directory_pattern))
            for prefix in prefixes
        )
    return any(
        fnmatchcase(prefix, pattern)
        or fnmatchcase(f"./{prefix}", pattern)
        or bool(
            directory_pattern
            and (
                fnmatchcase(prefix, directory_pattern)
                or fnmatchcase(f"./{prefix}", directory_pattern)
            )
        )
        for prefix in prefixes
    )


def path_matches_patterns(path: str | Path, patterns: Sequence[str]) -> bool:
    """Apply ordered gitignore-like patterns; later negations can re-include a path."""
    excluded = False
    normalized = _normalize_relative_path(path)
    for raw_pattern in patterns:
        pattern = str(raw_pattern).strip()
        if not pattern or pattern.startswith("#"):
            continue
        if _pattern_matches(normalized, pattern):
            excluded = not pattern.startswith("!")
    return excluded


def _path_matches_rule_groups(
    path: str | Path,
    mandatory_patterns: Sequence[str],
    additional_patterns: Sequence[str],
) -> bool:
    """Keep project rules from reopening mandatory credential exclusions."""
    return path_matches_patterns(path, mandatory_patterns) or path_matches_patterns(
        path, additional_patterns
    )


def _read_exclusion_file(root: Path) -> tuple[str, ...]:
    """Read local exclusion patterns if the ignore file is small and regular."""
    path = root / EXCLUSION_FILE_NAME
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_IGNORE_FILE_BYTES:
            return ()
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    return tuple(line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#"))


def _is_nested_repository(path: Path) -> bool:
    return any(os.path.lexists(path / marker) for marker in CONTROL_DIRECTORY_NAMES)


def _is_outside_symlink(root: Path, path: Path) -> bool:
    if not path.is_symlink():
        return False
    try:
        path.resolve(strict=True).relative_to(root)
        return False
    except (OSError, ValueError):
        return True


@dataclass(frozen=True)
class RepositoryExclusions:
    root: Path
    patterns: tuple[str, ...]
    nested_repository_roots: tuple[str, ...]
    protected_paths: tuple[Path, ...]
    mandatory_patterns: tuple[str, ...] = ()
    additional_patterns: tuple[str, ...] = ()

    @classmethod
    def discover(
        cls,
        root: Path,
        configured_patterns: Iterable[str] = (),
        *,
        exclude_nested_repositories: bool = True,
    ) -> "RepositoryExclusions":
        """Discover concrete protected paths without descending into them."""
        root = Path(root).resolve()
        mandatory_patterns = DEFAULT_EXCLUDED_PATHS
        additional_patterns = (
            *_read_exclusion_file(root),
            *(str(pattern) for pattern in configured_patterns),
        )
        patterns = (*mandatory_patterns, *additional_patterns)
        nested_roots: list[str] = []
        protected: list[Path] = []
        visited_directories = 0

        for current_root, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current = Path(current_root)
            kept_directories: list[str] = []
            for name in directory_names:
                child = current / name
                try:
                    relative = child.relative_to(root).as_posix()
                except ValueError:
                    continue

                if name in CONTROL_DIRECTORY_NAMES:
                    # Current-workspace VCS metadata remains available to Git,
                    # but Stringbean never traverses it for context discovery.
                    continue
                if _path_matches_rule_groups(relative, mandatory_patterns, additional_patterns):
                    protected.append(child)
                    continue
                if _is_outside_symlink(root, child):
                    # A symlink that resolves outside the workspace is treated
                    # as protected even if its link name looks harmless.
                    protected.extend((child, child.resolve(strict=False)))
                    continue
                if exclude_nested_repositories and _is_nested_repository(child):
                    nested_roots.append(relative)
                    protected.append(child)
                    continue

                visited_directories += 1
                if visited_directories >= _MAX_DISCOVERY_DIRECTORIES:
                    continue
                kept_directories.append(name)
            directory_names[:] = kept_directories

            for name in file_names:
                child = current / name
                try:
                    relative = child.relative_to(root).as_posix()
                except ValueError:
                    continue
                if _path_matches_rule_groups(relative, mandatory_patterns, additional_patterns):
                    protected.append(child)
                elif _is_outside_symlink(root, child):
                    protected.extend((child, child.resolve(strict=False)))

        unique_protected: dict[str, Path] = {}
        for path in protected:
            resolved = path.resolve(strict=False)
            unique_protected[str(resolved)] = resolved

        return cls(
            root=root,
            patterns=tuple(patterns),
            nested_repository_roots=tuple(sorted(set(nested_roots))),
            protected_paths=tuple(unique_protected[key] for key in sorted(unique_protected)),
            mandatory_patterns=tuple(mandatory_patterns),
            additional_patterns=tuple(additional_patterns),
        )

    def relative_path(self, path: str | Path) -> str | None:
        candidate = Path(path)
        if not candidate.is_absolute():
            return _normalize_relative_path(candidate)
        try:
            return candidate.resolve(strict=False).relative_to(self.root).as_posix()
        except ValueError:
            return None

    def is_excluded(self, path: str | Path) -> bool:
        """Return true for paths outside the root or covered by exclusion rules."""
        relative = self.relative_path(path)
        if relative is None:
            return True
        for nested_root in self.nested_repository_roots:
            if relative == nested_root or relative.startswith(f"{nested_root}/"):
                return True
        if self.mandatory_patterns or self.additional_patterns:
            return _path_matches_rule_groups(
                relative,
                self.mandatory_patterns,
                self.additional_patterns,
            )
        # Preserve behavior for callers that directly constructed the older
        # four-field dataclass instead of using ``discover``.
        return path_matches_patterns(relative, self.patterns)

    def prompt_patterns(self) -> tuple[str, ...]:
        visible = list(self.patterns)
        visible.extend(f"/{path}/** (nested repository)" for path in self.nested_repository_roots)
        return tuple(dict.fromkeys(visible))

    def encoded_protected_paths(self) -> str:
        # ASCII unit separator avoids ambiguity with the usual ':' and ',' in
        # legal filenames. NUL cannot be carried in an environment variable.
        return "\x1f".join(str(path) for path in self.protected_paths)


def is_control_metadata_path(path: str | Path) -> bool:
    return any(part in CONTROL_DIRECTORY_NAMES for part in Path(path).parts)
