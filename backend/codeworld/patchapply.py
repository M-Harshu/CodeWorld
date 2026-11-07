"""Pure-Python unified-diff applier (no `git apply`), tolerant of CRLF and zero-context hunks.

This replaces `git apply` which was fragile on Windows due to CRLF line endings.
It parses unified diffs and applies them cleanly to the target files.

Public API
----------
apply_patch_text(src_root, diff_text) -> (bool, str)
    Apply *diff_text* to the directory rooted at *src_root*.
    Returns (True, "applied to a scratch copy") on success,
    or (False, error_message) on failure.
"""
from __future__ import annotations
import re
from pathlib import Path

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _parse_diff(text: str) -> list[dict]:
    """Parse a unified diff into a list of file patches."""
    files: list[dict] = []
    cur: dict | None = None

    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("diff --git"):
            m = re.match(r"diff --git a/(.*?) b/(.*)$", line)
            cur = {
                "path": m.group(2) if m else None,
                "new_file": False,
                "deleted": False,
                "hunks": [],
            }
            files.append(cur)
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur["new_file"] = True
        elif line.startswith("deleted file mode"):
            cur["deleted"] = True
        elif line.startswith("+++ b/") and cur.get("path") is None:
            cur["path"] = line[6:].strip()
        elif line.startswith("@@") and cur is not None:
            m = _HUNK.match(line)
            if m:
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) is not None else 1
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) is not None else 1
                cur["hunks"].append({
                    "old_start": old_start,
                    "old_count": old_count,
                    "new_start": new_start,
                    "new_count": new_count,
                    "lines": [],
                })
        elif cur and cur["hunks"]:
            cur["hunks"][-1]["lines"].append(line)

    return [f for f in files if f.get("path")]


def _apply_hunk(src_lines: list[str], hunk: dict) -> list[str] | None:
    """Apply a single hunk to *src_lines*, returning new lines or None on failure."""
    old_start = hunk["old_start"]
    old_count = hunk["old_count"]
    raw = hunk["lines"]

    old_lines: list[str] = []
    new_lines: list[str] = []
    for line in raw:
        if line.startswith("-"):
            old_lines.append(line[1:])
        elif line.startswith("+"):
            new_lines.append(line[1:])
        elif line.startswith(" "):
            old_lines.append(line[1:])
            new_lines.append(line[1:])
        elif line == "":
            if len(old_lines) < old_count:
                old_lines.append("")
                new_lines.append("")
        elif not line.startswith("\\"):
            old_lines.append(line)
            new_lines.append(line)

    if old_count == 0:
        pos = 0 if old_start == 0 else old_start
    else:
        pos = max(0, old_start - 1)

    def matches_at(offset: int) -> bool:
        if offset < 0 or offset + len(old_lines) > len(src_lines):
            return False
        for i, exp in enumerate(old_lines):
            if src_lines[offset + i].rstrip("\r\n") != exp.rstrip("\r\n"):
                return False
        return True

    target_pos = None
    if matches_at(pos):
        target_pos = pos
    else:
        # Fuzz search within ±15 lines if offsets shifted
        for delta in range(1, 16):
            if matches_at(pos - delta):
                target_pos = pos - delta
                break
            if matches_at(pos + delta):
                target_pos = pos + delta
                break

    if target_pos is None:
        if old_count == 0 or len(old_lines) == 0:
            target_pos = min(pos, len(src_lines))
        else:
            return None

    return src_lines[:target_pos] + new_lines + src_lines[target_pos + len(old_lines):]


def apply_patch_text(src_root: str | Path, diff_text: str) -> tuple[bool, str]:
    """Apply *diff_text* to the directory rooted at *src_root*.

    Verifies all hunks across all modified files before writing changes.
    Returns (True, "applied to a scratch copy") on success,
    or (False, error_message) on failure.
    """
    src_root = Path(src_root)
    patches = _parse_diff(diff_text)
    if not patches:
        return False, "no files found in diff"

    # Security check: reject any path traversal attempts
    for fp in patches:
        p = fp["path"]
        if p and (".." in Path(p).parts or Path(p).is_absolute()):
            return False, f"unsafe path in diff: {p!r}"

    # First pass: compute all file updates in memory to ensure atomic success
    file_updates: dict[Path, str | None] = {}
    errors: list[str] = []

    for fp in patches:
        rel_path = fp["path"]
        target_file = src_root / rel_path

        if fp["deleted"]:
            file_updates[target_file] = None
            continue

        if fp["new_file"] or not target_file.exists():
            content_lines: list[str] = []
            for hunk in fp["hunks"]:
                for line in hunk["lines"]:
                    if line.startswith("+"):
                        content_lines.append(line[1:])
            file_updates[target_file] = "\n".join(content_lines) + ("\n" if content_lines else "")
            continue

        try:
            raw = target_file.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            errors.append(f"{rel_path}: {e}")
            continue

        lines = raw.replace("\r\n", "\n").split("\n")
        had_trailing_newline = raw.endswith("\n")
        if had_trailing_newline and lines and lines[-1] == "":
            lines = lines[:-1]

        # Apply hunks in reverse order of line position so later hunks do not invalidate earlier line indices
        sorted_hunks = sorted(fp["hunks"], key=lambda h: (h["old_start"], h["new_start"]), reverse=True)
        for hunk in sorted_hunks:
            res = _apply_hunk(lines, hunk)
            if res is None:
                errors.append(
                    f"{rel_path}: hunk @@ -{hunk['old_start']},{hunk['old_count']} "
                    f"+{hunk['new_start']},{hunk['new_count']} @@ did not apply cleanly"
                )
                break
            lines = res

        if had_trailing_newline:
            file_updates[target_file] = "\n".join(lines) + "\n"
        else:
            file_updates[target_file] = "\n".join(lines)

    if errors:
        return False, "\n".join(errors)

    # Second pass: commit all changes to disk
    try:
        for path, new_content in file_updates.items():
            if new_content is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(new_content, encoding="utf-8")
        return True, "applied to a scratch copy"
    except OSError as e:
        return False, f"failed to write patched files: {e}"
