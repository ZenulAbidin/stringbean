from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Mapping, Sequence


EXECUTION_PROFILES = {"ro", "rw"}

DENIED_COMMANDS: tuple[str, ...] = (
    "rm",
    "rmdir",
    "sudo",
    "su",
    "dd",
    "mkfs",
    "mount",
    "umount",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "systemctl",
    "service",
    "kill",
    "killall",
    "pkill",
    "chown",
    "chgrp",
    "setfacl",
    "shred",
    "wipefs",
)

DENIED_GIT_SUBCOMMANDS: tuple[str, ...] = (
    "reset",
    "clean",
    "checkout",
    "restore",
    "switch",
    "rebase",
    "merge",
    "commit",
    "push",
    "pull",
)


_REAL_GIT: str | None = None
POLICY_BIN_SENTINEL = ".stringbean-policy-bin"
POLICY_PRELOAD_NAME = "libstringbean_policy.so"
POLICY_ENV_PREFIX = "STRINGBEAN_POLICY_"


def _is_policy_bin_entry(path_entry: str) -> bool:
    if not path_entry:
        return False
    path = Path(path_entry)
    if path.name != "policy-bin":
        return False
    if (path / POLICY_BIN_SENTINEL).is_file():
        return True
    git_wrapper = path / "git"
    try:
        return git_wrapper.is_file() and "stringbean policy:" in git_wrapper.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def path_without_policy_bins(path: str | None = None) -> str:
    """Return PATH with Stringbean policy wrapper directories removed."""
    raw_path = os.environ.get("PATH", "") if path is None else path
    return os.pathsep.join(part for part in raw_path.split(os.pathsep) if not _is_policy_bin_entry(part))


def _is_policy_preload_entry(preload_entry: str, env: Mapping[str, str]) -> bool:
    if not preload_entry:
        return False
    configured_preload = env.get("STRINGBEAN_POLICY_PRELOAD")
    if configured_preload and preload_entry == configured_preload:
        return True
    path = Path(preload_entry)
    if path.name != POLICY_PRELOAD_NAME:
        return False
    return _is_policy_bin_entry(str(path.parent))


def ld_preload_without_policy_entries(ld_preload: str | None, env: Mapping[str, str]) -> str:
    """Return LD_PRELOAD with Stringbean policy preload entries removed."""
    if not ld_preload:
        return ""
    entries = [entry for group in ld_preload.split() for entry in group.split(os.pathsep) if entry]
    return " ".join(entry for entry in entries if not _is_policy_preload_entry(entry, env))


def internal_subprocess_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for Stringbean-owned subprocesses that must not use policy wrappers."""
    out = dict(os.environ if env is None else env)
    out["PATH"] = path_without_policy_bins(out.get("PATH", ""))
    cleaned_preload = ld_preload_without_policy_entries(out.get("LD_PRELOAD"), out)
    if cleaned_preload:
        out["LD_PRELOAD"] = cleaned_preload
    else:
        out.pop("LD_PRELOAD", None)
    for name in tuple(out):
        if name.startswith(POLICY_ENV_PREFIX):
            out.pop(name, None)
    return out


def resolve_real_git() -> str | None:
    """Resolve and cache git outside generated policy-bin PATH shims."""
    global _REAL_GIT
    if _REAL_GIT is not None:
        return _REAL_GIT
    real_git = shutil.which("git", path=path_without_policy_bins())
    if real_git:
        _REAL_GIT = real_git
    return _REAL_GIT


def git_command() -> str:
    return resolve_real_git() or "git"


def _csv_env_set(env: Mapping[str, str], name: str, default: Iterable[str]) -> set[str]:
    raw = env.get(name)
    if raw is None:
        return {part for part in default if part}
    return {part.strip() for part in raw.split(",") if part.strip()}


def _command_basenames(command: str, env: Mapping[str, str]) -> set[str]:
    path = Path(command)
    names = {path.name}
    if path.is_absolute() or os.sep in command:
        try:
            names.add(path.resolve(strict=True).name)
        except OSError:
            pass
    else:
        resolved = shutil.which(command, path=env.get("PATH"))
        if resolved:
            try:
                names.add(Path(resolved).resolve(strict=True).name)
            except OSError:
                names.add(Path(resolved).name)
    return {name for name in names if name}


def _git_subcommand(argv: Sequence[str]) -> str | None:
    idx = 1
    while idx < len(argv):
        arg = str(argv[idx])
        if arg in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}:
            idx += 2
            continue
        if arg.startswith(("--git-dir=", "--work-tree=", "--namespace=", "--config-env=")):
            idx += 1
            continue
        if arg.startswith("-c") and arg != "-c":
            idx += 1
            continue
        if arg == "--exec-path":
            idx += 2 if idx + 1 < len(argv) and not str(argv[idx + 1]).startswith("-") else 1
            continue
        if arg.startswith("--exec-path=") or arg in {
            "--no-pager",
            "--paginate",
            "-p",
            "--bare",
            "--no-replace-objects",
            "--literal-pathspecs",
            "--glob-pathspecs",
            "--noglob-pathspecs",
            "--icase-pathspecs",
            "--no-optional-locks",
        }:
            idx += 1
            continue
        if arg == "--":
            return str(argv[idx + 1]) if idx + 1 < len(argv) else None
        if arg.startswith("-"):
            idx += 1
            continue
        return arg
    return None


def command_policy_denial(command: Sequence[str], env: Mapping[str, str] | None = None) -> str | None:
    if not command:
        return None
    policy_env = os.environ if env is None else env
    denied_commands = _csv_env_set(policy_env, "STRINGBEAN_DENIED_COMMANDS", DENIED_COMMANDS)
    denied_git_subcommands = _csv_env_set(policy_env, "STRINGBEAN_DENIED_GIT_SUBCOMMANDS", DENIED_GIT_SUBCOMMANDS)
    basenames = _command_basenames(str(command[0]), policy_env)
    denied_basename = next((name for name in basenames if name in denied_commands), None)
    if denied_basename is not None:
        return f"stringbean policy: command '{denied_basename}' is denied for subagents."
    if "git" in basenames:
        subcommand = _git_subcommand(command)
        if subcommand in denied_git_subcommands:
            return f"stringbean policy: this git operation is denied for subagents: git {subcommand}"
    git_helper = next((name for name in basenames if name.startswith("git-")), None)
    if git_helper is not None:
        subcommand = git_helper[4:]
        if subcommand in denied_git_subcommands:
            return f"stringbean policy: this git operation is denied for subagents: git {subcommand}"
    return None


def normalize_execution_profile(value: str | None) -> str:
    normalized = (value or "rw").strip().lower()
    if normalized not in EXECUTION_PROFILES:
        raise ValueError("execution profile must be ro or rw")
    return normalized


def codex_sandbox_for_profile(profile: str) -> str:
    profile = normalize_execution_profile(profile)
    if profile == "rw":
        return "danger-full-access"
    return "workspace-write"


def apply_codex_execution_profile(command: Sequence[str], profile: str) -> list[str]:
    """Force Codex subprocesses into Stringbean's explicit execution profile."""
    out: list[str] = []
    skip_next = False
    for part in command:
        if skip_next:
            skip_next = False
            continue
        if part in {"-a", "--ask-for-approval", "-s", "--sandbox"}:
            skip_next = True
            continue
        if part.startswith("--ask-for-approval=") or part.startswith("--sandbox="):
            continue
        if part == "--dangerously-bypass-approvals-and-sandbox":
            continue
        out.append(str(part))

    if len(out) >= 2 and Path(out[0]).name == "codex" and "exec" in out[1:]:
        exec_index = out.index("exec", 1)
        exec_args = out[exec_index + 1 :]
        if "--skip-git-repo-check" not in exec_args:
            exec_args = ["--skip-git-repo-check", *exec_args]
        return [
            out[0],
            "--ask-for-approval",
            "never",
            "--sandbox",
            codex_sandbox_for_profile(profile),
            *out[1:exec_index],
            out[exec_index],
            *exec_args,
        ]
    return out


def policy_prompt(
    profile: str,
    effective_permission: str,
    *,
    workspace_root: Path | None = None,
    excluded_paths: Sequence[str] = (),
) -> str:
    profile = normalize_execution_profile(profile)
    denied = ", ".join(DENIED_COMMANDS)
    denied_git = ", ".join(f"git {name}" for name in DENIED_GIT_SUBCOMMANDS)
    if profile == "rw":
        write_policy = (
            "Execution profile: rw. Agents with read_write permission may modify files in service "
            "of the task. Agents with read_only permission must not modify files; Stringbean will "
            "treat modifications as a policy violation."
        )
    else:
        write_policy = (
            "Execution profile: ro. Treat this run as create-only. You may create new files or "
            "new directories, but you must not modify, delete, rename, move, or type-change "
            "pre-existing repository paths. Stringbean will treat forbidden changes as policy "
            "violations, even for agents whose configured role is read_write."
        )
    scope_policy = ""
    if workspace_root is not None:
        scope_policy += (
            f"\n- Default workspace boundary: {Path(workspace_root).resolve()}. Do not inspect unrelated "
            "parent or sibling paths. A path explicitly named by the user's task is in scope unless "
            "an excluded-path rule protects it."
        )
    if excluded_paths:
        rendered_exclusions = ", ".join(str(path) for path in excluded_paths)
        scope_policy += (
            "\n- Ordered excluded-path rules (`!` means an allowed exception): "
            f"{rendered_exclusions}."
            "\n- Never read, list, search, summarize, modify, or transmit content from paths excluded "
            "by those rules."
            "\n- If an excluded path appears relevant, skip it and continue with the rest of the task. "
            "Do not retry access and do not ask another agent to inspect it."
        )
    return (
        "Stringbean execution policy:\n"
        f"- {write_policy}\n"
        f"- Effective permission for this call: {effective_permission}.\n"
        f"- Do not run these denied commands: {denied}.\n"
        f"- Do not run these denied git operations: {denied_git}.\n"
        "- If a denied operation appears necessary, stop and report it instead of running it."
        f"{scope_policy}"
    )


def _write_policy_preload_source(source_path: Path) -> None:
    source_path.write_text(
        r'''
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <spawn.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>

extern char **environ;

static __thread int sb_access_guard = 0;

static const char *sb_basename(const char *path) {
    const char *slash;
    if (path == NULL) {
        return "";
    }
    slash = strrchr(path, '/');
    return slash == NULL ? path : slash + 1;
}

static const char *sb_resolved_basename(const char *path, char *resolved, size_t resolved_size) {
    const char *path_env;
    char *path_copy;
    char *saveptr = NULL;
    char *part;
    if (path == NULL) {
        return NULL;
    }
    if (strchr(path, '/') != NULL) {
        return realpath(path, resolved) == NULL ? NULL : sb_basename(resolved);
    }
    path_env = getenv("PATH");
    if (path_env == NULL) {
        return NULL;
    }
    path_copy = strdup(path_env);
    if (path_copy == NULL) {
        return NULL;
    }
    for (part = strtok_r(path_copy, ":", &saveptr); part != NULL; part = strtok_r(NULL, ":", &saveptr)) {
        char candidate[PATH_MAX];
        const char *dir = part[0] == '\0' ? "." : part;
        int written = snprintf(candidate, sizeof(candidate), "%s/%s", dir, path);
        if (written < 0 || (size_t)written >= sizeof(candidate)) {
            continue;
        }
        if (access(candidate, X_OK) == 0 && realpath(candidate, resolved) != NULL) {
            const char *base = sb_basename(resolved);
            free(path_copy);
            return base;
        }
    }
    free(path_copy);
    (void)resolved_size;
    return NULL;
}

static int sb_list_contains(const char *list, const char *needle) {
    const char *start;
    size_t needle_len;
    if (list == NULL || needle == NULL || needle[0] == '\0') {
        return 0;
    }
    needle_len = strlen(needle);
    start = list;
    while (*start != '\0') {
        const char *end = strchr(start, ',');
        size_t len = end == NULL ? strlen(start) : (size_t)(end - start);
        while (len > 0 && (*start == ' ' || *start == '\t')) {
            start++;
            len--;
        }
        while (len > 0 && (start[len - 1] == ' ' || start[len - 1] == '\t')) {
            len--;
        }
        if (len == needle_len && strncmp(start, needle, len) == 0) {
            return 1;
        }
        if (end == NULL) {
            break;
        }
        start = end + 1;
    }
    return 0;
}

static const char *sb_git_subcommand(char *const argv[]) {
    int idx = 1;
    if (argv == NULL) {
        return NULL;
    }
    while (argv[idx] != NULL) {
        const char *arg = argv[idx];
        if (
            strcmp(arg, "-C") == 0 ||
            strcmp(arg, "-c") == 0 ||
            strcmp(arg, "--git-dir") == 0 ||
            strcmp(arg, "--work-tree") == 0 ||
            strcmp(arg, "--namespace") == 0 ||
            strcmp(arg, "--config-env") == 0
        ) {
            idx += argv[idx + 1] == NULL ? 1 : 2;
            continue;
        }
        if (
            strncmp(arg, "--git-dir=", 10) == 0 ||
            strncmp(arg, "--work-tree=", 12) == 0 ||
            strncmp(arg, "--namespace=", 12) == 0 ||
            strncmp(arg, "--config-env=", 13) == 0 ||
            (strncmp(arg, "-c", 2) == 0 && strcmp(arg, "-c") != 0)
        ) {
            idx++;
            continue;
        }
        if (strcmp(arg, "--exec-path") == 0) {
            idx += argv[idx + 1] != NULL && argv[idx + 1][0] != '-' ? 2 : 1;
            continue;
        }
        if (
            strncmp(arg, "--exec-path=", 12) == 0 ||
            strcmp(arg, "--no-pager") == 0 ||
            strcmp(arg, "--paginate") == 0 ||
            strcmp(arg, "-p") == 0 ||
            strcmp(arg, "--bare") == 0 ||
            strcmp(arg, "--no-replace-objects") == 0 ||
            strcmp(arg, "--literal-pathspecs") == 0 ||
            strcmp(arg, "--glob-pathspecs") == 0 ||
            strcmp(arg, "--noglob-pathspecs") == 0 ||
            strcmp(arg, "--icase-pathspecs") == 0 ||
            strcmp(arg, "--no-optional-locks") == 0
        ) {
            idx++;
            continue;
        }
        if (strcmp(arg, "--") == 0) {
            return argv[idx + 1];
        }
        if (arg[0] == '-') {
            idx++;
            continue;
        }
        return arg;
    }
    return NULL;
}

static int sb_should_block(const char *path, char *const argv[]) {
    const char *base = sb_basename(path);
    char resolved[PATH_MAX];
    const char *resolved_base = sb_resolved_basename(path, resolved, sizeof(resolved));
    const char *denied_commands = getenv("STRINGBEAN_DENIED_COMMANDS");
    const char *denied_git = getenv("STRINGBEAN_DENIED_GIT_SUBCOMMANDS");
    if (sb_list_contains(denied_commands, base) || sb_list_contains(denied_commands, resolved_base)) {
        const char *denied = sb_list_contains(denied_commands, base) ? base : resolved_base;
        fprintf(stderr, "stringbean policy: command '%s' is denied for subagents.\n", denied);
        return 1;
    }
    if (strcmp(base, "git") == 0 || (resolved_base != NULL && strcmp(resolved_base, "git") == 0)) {
        const char *subcommand = sb_git_subcommand(argv);
        if (sb_list_contains(denied_git, subcommand)) {
            fprintf(stderr, "stringbean policy: this git operation is denied for subagents: git %s\n", subcommand);
            return 1;
        }
    }
    if (strncmp(base, "git-", 4) == 0 && sb_list_contains(denied_git, base + 4)) {
        fprintf(stderr, "stringbean policy: this git operation is denied for subagents: git %s\n", base + 4);
        return 1;
    }
    if (resolved_base != NULL && strncmp(resolved_base, "git-", 4) == 0 && sb_list_contains(denied_git, resolved_base + 4)) {
        fprintf(stderr, "stringbean policy: this git operation is denied for subagents: git %s\n", resolved_base + 4);
        return 1;
    }
    return 0;
}

static int sb_absolute_path(const char *path, int dirfd, char *output, size_t output_size) {
    char combined[PATH_MAX];
    char base[PATH_MAX];
    char proc_path[64];
    ssize_t base_len;
    int written;
    if (path == NULL || path[0] == '\0') {
        return 0;
    }
    if (path[0] == '/') {
        written = snprintf(combined, sizeof(combined), "%s", path);
    } else {
        if (dirfd == AT_FDCWD) {
            if (getcwd(base, sizeof(base)) == NULL) {
                return 0;
            }
        } else {
            written = snprintf(proc_path, sizeof(proc_path), "/proc/self/fd/%d", dirfd);
            if (written < 0 || (size_t)written >= sizeof(proc_path)) {
                return 0;
            }
            base_len = readlink(proc_path, base, sizeof(base) - 1);
            if (base_len < 0 || (size_t)base_len >= sizeof(base) - 1) {
                return 0;
            }
            base[base_len] = '\0';
        }
        written = snprintf(combined, sizeof(combined), "%s/%s", base, path);
    }
    if (written < 0 || (size_t)written >= sizeof(combined)) {
        return 0;
    }
    if (realpath(combined, output) != NULL) {
        return 1;
    }
    written = snprintf(output, output_size, "%s", combined);
    return written >= 0 && (size_t)written < output_size;
}

static int sb_path_list_contains(const char *list, const char *path) {
    const char *start;
    size_t path_len;
    if (list == NULL || list[0] == '\0' || path == NULL || path[0] == '\0') {
        return 0;
    }
    path_len = strlen(path);
    start = list;
    while (*start != '\0') {
        const char *end = strchr(start, 0x1f);
        size_t len = end == NULL ? strlen(start) : (size_t)(end - start);
        if (
            len > 0 &&
            path_len >= len &&
            strncmp(start, path, len) == 0 &&
            (path_len == len || path[len] == '/')
        ) {
            return 1;
        }
        if (end == NULL) {
            break;
        }
        start = end + 1;
    }
    return 0;
}

static int sb_should_block_access(const char *path, int dirfd) {
    char absolute[PATH_MAX];
    const char *allowed_paths;
    const char *excluded_paths;
    int blocked;
    if (sb_access_guard) {
        return 0;
    }
    excluded_paths = getenv("STRINGBEAN_POLICY_EXCLUDED_PATHS");
    if (excluded_paths == NULL || excluded_paths[0] == '\0') {
        return 0;
    }
    sb_access_guard = 1;
    allowed_paths = getenv("STRINGBEAN_POLICY_ALLOWED_PATHS");
    blocked = sb_absolute_path(path, dirfd, absolute, sizeof(absolute)) &&
        !sb_path_list_contains(allowed_paths, absolute) &&
        sb_path_list_contains(excluded_paths, absolute);
    sb_access_guard = 0;
    if (blocked) {
        fprintf(stderr, "stringbean policy: excluded path access denied; skip it without retrying.\n");
    }
    return blocked;
}

static int sb_open_needs_mode(int flags) {
    if ((flags & O_CREAT) != 0) {
        return 1;
    }
#ifdef O_TMPFILE
    if ((flags & O_TMPFILE) == O_TMPFILE) {
        return 1;
    }
#endif
    return 0;
}

int open(const char *pathname, int flags, ...) {
    static int (*real_open)(const char *, int, ...) = NULL;
    mode_t mode = 0;
    if (sb_open_needs_mode(flags)) {
        va_list args;
        va_start(args, flags);
        mode = va_arg(args, mode_t);
        va_end(args);
    }
    if (sb_should_block_access(pathname, AT_FDCWD)) {
        errno = EACCES;
        return -1;
    }
    if (real_open == NULL) {
        real_open = dlsym(RTLD_NEXT, "open");
    }
    return sb_open_needs_mode(flags) ? real_open(pathname, flags, mode) : real_open(pathname, flags);
}

int open64(const char *pathname, int flags, ...) {
    static int (*real_open64)(const char *, int, ...) = NULL;
    mode_t mode = 0;
    if (sb_open_needs_mode(flags)) {
        va_list args;
        va_start(args, flags);
        mode = va_arg(args, mode_t);
        va_end(args);
    }
    if (sb_should_block_access(pathname, AT_FDCWD)) {
        errno = EACCES;
        return -1;
    }
    if (real_open64 == NULL) {
        real_open64 = dlsym(RTLD_NEXT, "open64");
    }
    return sb_open_needs_mode(flags) ? real_open64(pathname, flags, mode) : real_open64(pathname, flags);
}

int openat(int dirfd, const char *pathname, int flags, ...) {
    static int (*real_openat)(int, const char *, int, ...) = NULL;
    mode_t mode = 0;
    if (sb_open_needs_mode(flags)) {
        va_list args;
        va_start(args, flags);
        mode = va_arg(args, mode_t);
        va_end(args);
    }
    if (sb_should_block_access(pathname, dirfd)) {
        errno = EACCES;
        return -1;
    }
    if (real_openat == NULL) {
        real_openat = dlsym(RTLD_NEXT, "openat");
    }
    return sb_open_needs_mode(flags) ? real_openat(dirfd, pathname, flags, mode) : real_openat(dirfd, pathname, flags);
}

int openat64(int dirfd, const char *pathname, int flags, ...) {
    static int (*real_openat64)(int, const char *, int, ...) = NULL;
    mode_t mode = 0;
    if (sb_open_needs_mode(flags)) {
        va_list args;
        va_start(args, flags);
        mode = va_arg(args, mode_t);
        va_end(args);
    }
    if (sb_should_block_access(pathname, dirfd)) {
        errno = EACCES;
        return -1;
    }
    if (real_openat64 == NULL) {
        real_openat64 = dlsym(RTLD_NEXT, "openat64");
    }
    return sb_open_needs_mode(flags) ? real_openat64(dirfd, pathname, flags, mode) : real_openat64(dirfd, pathname, flags);
}

FILE *fopen(const char *pathname, const char *mode) {
    static FILE *(*real_fopen)(const char *, const char *) = NULL;
    if (sb_should_block_access(pathname, AT_FDCWD)) {
        errno = EACCES;
        return NULL;
    }
    if (real_fopen == NULL) {
        real_fopen = dlsym(RTLD_NEXT, "fopen");
    }
    return real_fopen(pathname, mode);
}

FILE *fopen64(const char *pathname, const char *mode) {
    static FILE *(*real_fopen64)(const char *, const char *) = NULL;
    if (sb_should_block_access(pathname, AT_FDCWD)) {
        errno = EACCES;
        return NULL;
    }
    if (real_fopen64 == NULL) {
        real_fopen64 = dlsym(RTLD_NEXT, "fopen64");
    }
    return real_fopen64(pathname, mode);
}

DIR *opendir(const char *name) {
    static DIR *(*real_opendir)(const char *) = NULL;
    if (sb_should_block_access(name, AT_FDCWD)) {
        errno = EACCES;
        return NULL;
    }
    if (real_opendir == NULL) {
        real_opendir = dlsym(RTLD_NEXT, "opendir");
    }
    return real_opendir(name);
}

static const char *const sb_preserved_env_names[] = {
    "LD_PRELOAD",
    "STRINGBEAN_POLICY_BIN",
    "STRINGBEAN_POLICY_PRELOAD",
    "STRINGBEAN_POLICY_PRELOAD_ACTIVE",
    "STRINGBEAN_POLICY_WRAPPERS_ACTIVE",
    "STRINGBEAN_POLICY_EXCLUDED_PATHS",
    "STRINGBEAN_POLICY_ALLOWED_PATHS",
    "STRINGBEAN_DENIED_COMMANDS",
    "STRINGBEAN_DENIED_GIT_SUBCOMMANDS",
    NULL
};

static int sb_env_entry_has_name(const char *entry, const char *name) {
    size_t len;
    if (entry == NULL || name == NULL) {
        return 0;
    }
    len = strlen(name);
    return strncmp(entry, name, len) == 0 && entry[len] == '=';
}

static int sb_is_preserved_env_entry(const char *entry) {
    size_t idx;
    for (idx = 0; sb_preserved_env_names[idx] != NULL; idx++) {
        if (sb_env_entry_has_name(entry, sb_preserved_env_names[idx])) {
            return 1;
        }
    }
    return 0;
}

static void sb_free_hardened_env(
    char **environment,
    size_t appended_start,
    size_t appended_count
) {
    size_t idx;
    if (environment == NULL) {
        return;
    }
    for (idx = 0; idx < appended_count; idx++) {
        free(environment[appended_start + idx]);
    }
    free(environment);
}

static int sb_harden_child_env(
    char *const envp[],
    char ***output,
    size_t *appended_start,
    size_t *appended_count
) {
    const char *policy_preload = getenv("STRINGBEAN_POLICY_PRELOAD");
    size_t input_count = 0;
    size_t preserved_count = 0;
    size_t kept_count = 0;
    size_t idx;
    char **hardened;
    if (policy_preload == NULL || policy_preload[0] == '\0') {
        *output = NULL;
        *appended_start = 0;
        *appended_count = 0;
        return 0;
    }
    if (envp != NULL) {
        while (envp[input_count] != NULL) {
            input_count++;
        }
    }
    for (idx = 0; sb_preserved_env_names[idx] != NULL; idx++) {
        const char *value = getenv(sb_preserved_env_names[idx]);
        if (value != NULL) {
            preserved_count++;
        }
    }
    hardened = calloc(input_count + preserved_count + 1, sizeof(char *));
    if (hardened == NULL) {
        return -1;
    }
    for (idx = 0; idx < input_count; idx++) {
        if (!sb_is_preserved_env_entry(envp[idx])) {
            hardened[kept_count++] = envp[idx];
        }
    }
    *appended_start = kept_count;
    *appended_count = 0;
    for (idx = 0; sb_preserved_env_names[idx] != NULL; idx++) {
        const char *name = sb_preserved_env_names[idx];
        const char *value = getenv(name);
        size_t length;
        char *entry;
        if (value == NULL) {
            continue;
        }
        length = strlen(name) + strlen(value) + 2;
        entry = malloc(length);
        if (entry == NULL) {
            sb_free_hardened_env(hardened, *appended_start, *appended_count);
            *output = NULL;
            return -1;
        }
        snprintf(entry, length, "%s=%s", name, value);
        hardened[kept_count++] = entry;
        (*appended_count)++;
    }
    hardened[kept_count] = NULL;
    *output = hardened;
    return 1;
}

int execve(const char *pathname, char *const argv[], char *const envp[]) {
    static int (*real_execve)(const char *, char *const[], char *const[]) = NULL;
    char **hardened_env = NULL;
    size_t appended_start = 0;
    size_t appended_count = 0;
    int harden_result;
    int result;
    if (sb_should_block(pathname, argv)) {
        errno = EACCES;
        return -1;
    }
    if (real_execve == NULL) {
        real_execve = dlsym(RTLD_NEXT, "execve");
    }
    harden_result = sb_harden_child_env(envp, &hardened_env, &appended_start, &appended_count);
    if (harden_result < 0) {
        errno = ENOMEM;
        return -1;
    }
    result = real_execve(pathname, argv, harden_result > 0 ? hardened_env : envp);
    sb_free_hardened_env(hardened_env, appended_start, appended_count);
    return result;
}

int execv(const char *path, char *const argv[]) {
    static int (*real_execv)(const char *, char *const[]) = NULL;
    if (sb_should_block(path, argv)) {
        errno = EACCES;
        return -1;
    }
    if (real_execv == NULL) {
        real_execv = dlsym(RTLD_NEXT, "execv");
    }
    return real_execv(path, argv);
}

int execvp(const char *file, char *const argv[]) {
    static int (*real_execvp)(const char *, char *const[]) = NULL;
    if (sb_should_block(file, argv)) {
        errno = EACCES;
        return -1;
    }
    if (real_execvp == NULL) {
        real_execvp = dlsym(RTLD_NEXT, "execvp");
    }
    return real_execvp(file, argv);
}

int execvpe(const char *file, char *const argv[], char *const envp[]) {
    static int (*real_execvpe)(const char *, char *const[], char *const[]) = NULL;
    char **hardened_env = NULL;
    size_t appended_start = 0;
    size_t appended_count = 0;
    int harden_result;
    int result;
    if (sb_should_block(file, argv)) {
        errno = EACCES;
        return -1;
    }
    if (real_execvpe == NULL) {
        real_execvpe = dlsym(RTLD_NEXT, "execvpe");
    }
    harden_result = sb_harden_child_env(envp, &hardened_env, &appended_start, &appended_count);
    if (harden_result < 0) {
        errno = ENOMEM;
        return -1;
    }
    result = real_execvpe(file, argv, harden_result > 0 ? hardened_env : envp);
    sb_free_hardened_env(hardened_env, appended_start, appended_count);
    return result;
}

int posix_spawn(
    pid_t *pid,
    const char *path,
    const posix_spawn_file_actions_t *file_actions,
    const posix_spawnattr_t *attrp,
    char *const argv[],
    char *const envp[]
) {
    static int (*real_posix_spawn)(pid_t *, const char *, const posix_spawn_file_actions_t *, const posix_spawnattr_t *, char *const[], char *const[]) = NULL;
    char **hardened_env = NULL;
    size_t appended_start = 0;
    size_t appended_count = 0;
    int harden_result;
    int result;
    if (sb_should_block(path, argv)) {
        return EACCES;
    }
    if (real_posix_spawn == NULL) {
        real_posix_spawn = dlsym(RTLD_NEXT, "posix_spawn");
    }
    harden_result = sb_harden_child_env(envp, &hardened_env, &appended_start, &appended_count);
    if (harden_result < 0) {
        return ENOMEM;
    }
    result = real_posix_spawn(
        pid,
        path,
        file_actions,
        attrp,
        argv,
        harden_result > 0 ? hardened_env : envp
    );
    sb_free_hardened_env(hardened_env, appended_start, appended_count);
    return result;
}

int posix_spawnp(
    pid_t *pid,
    const char *file,
    const posix_spawn_file_actions_t *file_actions,
    const posix_spawnattr_t *attrp,
    char *const argv[],
    char *const envp[]
) {
    static int (*real_posix_spawnp)(pid_t *, const char *, const posix_spawn_file_actions_t *, const posix_spawnattr_t *, char *const[], char *const[]) = NULL;
    char **hardened_env = NULL;
    size_t appended_start = 0;
    size_t appended_count = 0;
    int harden_result;
    int result;
    if (sb_should_block(file, argv)) {
        return EACCES;
    }
    if (real_posix_spawnp == NULL) {
        real_posix_spawnp = dlsym(RTLD_NEXT, "posix_spawnp");
    }
    harden_result = sb_harden_child_env(envp, &hardened_env, &appended_start, &appended_count);
    if (harden_result < 0) {
        return ENOMEM;
    }
    result = real_posix_spawnp(
        pid,
        file,
        file_actions,
        attrp,
        argv,
        harden_result > 0 ? hardened_env : envp
    );
    sb_free_hardened_env(hardened_env, appended_start, appended_count);
    return result;
}
'''.lstrip(),
        encoding="utf-8",
    )


def _compile_policy_preload(bin_dir: Path) -> Path | None:
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        return None
    source_path = bin_dir / "stringbean_policy_preload.c"
    library_path = bin_dir / POLICY_PRELOAD_NAME
    _write_policy_preload_source(source_path)
    proc = subprocess.run(
        [compiler, "-shared", "-fPIC", str(source_path), "-o", str(library_path), "-ldl"],
        check=False,
        capture_output=True,
        text=True,
        env=internal_subprocess_env(),
    )
    if proc.returncode != 0:
        try:
            library_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return library_path


def install_command_policy_wrappers(
    directory: Path,
    denied_commands: Iterable[str] = DENIED_COMMANDS,
    denied_git_subcommands: Iterable[str] = DENIED_GIT_SUBCOMMANDS,
) -> Path:
    """Create PATH shims that block common destructive commands for subagents."""
    bin_dir = directory / "policy-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / POLICY_BIN_SENTINEL).write_text("stringbean policy bin\n", encoding="utf-8")

    denied_text = ", ".join(sorted(set(denied_commands)))
    for command in sorted(set(denied_commands)):
        wrapper = bin_dir / command
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"stringbean policy: command '{command}' is denied for subagents.\" >&2\n"
            f"echo \"denied commands: {denied_text}\" >&2\n"
            "exit 126\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    real_git = resolve_real_git()
    if real_git:
        denied_git = "|".join(sorted(set(denied_git_subcommands)))
        git_wrapper = bin_dir / "git"
        git_wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "_stringbean_git_subcommand() {\n"
            "  while [[ $# -gt 0 ]]; do\n"
            "    case \"$1\" in\n"
            "      -C|-c|--git-dir|--work-tree|--namespace|--config-env)\n"
            "        if [[ $# -lt 2 ]]; then\n"
            "          return 0\n"
            "        fi\n"
            "        shift 2\n"
            "        ;;\n"
            "      --git-dir=*|--work-tree=*|--namespace=*|--config-env=*|-c*)\n"
            "        shift\n"
            "        ;;\n"
            "      --exec-path)\n"
            "        if [[ $# -ge 2 && ${2:-} != -* ]]; then\n"
            "          shift 2\n"
            "        else\n"
            "          shift\n"
            "        fi\n"
            "        ;;\n"
            "      --exec-path=*|--no-pager|--paginate|-p|--bare|--no-replace-objects|--literal-pathspecs|--glob-pathspecs|--noglob-pathspecs|--icase-pathspecs|--no-optional-locks)\n"
            "        shift\n"
            "        ;;\n"
            "      --)\n"
            "        shift\n"
            "        if [[ $# -gt 0 ]]; then\n"
            "          printf '%s\\n' \"$1\"\n"
            "        fi\n"
            "        return 0\n"
            "        ;;\n"
            "      -*)\n"
            "        shift\n"
            "        ;;\n"
            "      *)\n"
            "        printf '%s\\n' \"$1\"\n"
            "        return 0\n"
            "        ;;\n"
            "    esac\n"
            "  done\n"
            "}\n"
            "subcommand=\"$(_stringbean_git_subcommand \"$@\")\"\n"
            f"case \"$subcommand\" in\n"
            f"  {denied_git})\n"
            "    echo \"stringbean policy: this git operation is denied for subagents: git ${subcommand}\" >&2\n"
            "    exit 126\n"
            "    ;;\n"
            "esac\n"
            f"exec {real_git!r} \"$@\"\n",
            encoding="utf-8",
        )
        git_wrapper.chmod(0o755)

    _compile_policy_preload(bin_dir)
    return bin_dir
