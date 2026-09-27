"""사라진 데이터 수색.

가설 H5: 프로그램이 `C:\\Program Files (x86)` 하위에 설치되어 있으면,
윈도우 UAC 가상화가 그 폴더에 대한 쓰기를 조용히 가로채
`%LOCALAPPDATA%\\VirtualStore\\Program Files (x86)\\...` 로 돌린다.
프로그램은 저장에 성공했다고 믿는다. 하지만 읽을 때는 원래 경로를 본다.

그래서 '사라졌다'고 생각한 메모가 VirtualStore에 그대로 살아 있을 수 있다.
이 모듈은 그 사본을 찾아내고, 현재 데이터와 대조해 무엇이 더 들어 있는지 보여준다.

윈도우 전용 경로를 다루지만 다른 OS에서도 죽지 않는다. 그냥 찾을 게 없을 뿐이다.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# 파일명이나 폴더명에 이게 들어 있으면 관련 데이터로 본다.
NAME_HINTS = ("shock", "sticker", "쇼크", "스티커")
DATA_SUFFIXES = (".ssk", ".dat", ".ini", ".cfg", ".bak", ".db")

MAX_SCAN_DEPTH = 8
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "$RECYCLE.BIN", "System Volume Information"}


@dataclass
class Found:
    path: Path
    size: int
    mtime: datetime
    digest: str
    origin: str          # 어느 수색 경로에서 나왔는지

    def __str__(self) -> str:
        return (
            f"{self.mtime:%Y-%m-%d %H:%M}  {self.size:>10,}  "
            f"[{self.origin}]  {self.path}"
        )


def virtualstore_roots() -> list[Path]:
    """UAC 가상화 저장소 후보들."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    base = Path(local) / "VirtualStore"
    return [base] if base.is_dir() else []


def install_roots() -> list[Path]:
    """Shock Sticker가 설치되었을 법한 경로들."""
    candidates: list[Path] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(var)
        if value:
            candidates.append(Path(value))
    # 이관 대상 포함, 루트에 직접 깐 경우.
    for drive in "CDEF":
        for name in ("ShockSticker", "Shock Utility"):
            p = Path(f"{drive}:/{name}")
            if p.is_dir():
                candidates.append(p)
    return [p for p in candidates if p.is_dir()]


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while block := fh.read(1 << 16):
                h.update(block)
    except OSError:
        return ""
    return h.hexdigest()


def _is_interesting(path: Path) -> bool:
    lowered = path.name.lower()
    if path.suffix.lower() == ".ssk":
        return True
    if any(hint in lowered for hint in NAME_HINTS):
        return True
    # 상위 폴더 이름이 프로그램을 가리키고 확장자가 데이터성이면 가져온다.
    parents = " ".join(p.name.lower() for p in path.parents[:3])
    if any(hint in parents for hint in NAME_HINTS):
        return path.suffix.lower() in DATA_SUFFIXES
    return False


def scan(roots: list[Path], origin: str, max_depth: int = MAX_SCAN_DEPTH) -> list[Found]:
    """주어진 경로들을 훑어 관련 파일을 모은다."""
    results: list[Found] = []
    for root in roots:
        root = Path(root)
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
            here = Path(dirpath)
            if len(here.parts) - base_depth >= max_depth:
                dirnames.clear()
                continue
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                path = here / name
                if not _is_interesting(path):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                results.append(
                    Found(
                        path=path,
                        size=stat.st_size,
                        mtime=datetime.fromtimestamp(stat.st_mtime),
                        digest=_digest(path),
                        origin=origin,
                    )
                )
    return results


def sweep(extra_roots: list[Path] | None = None) -> list[Found]:
    """VirtualStore와 설치 경로를 모두 훑는다."""
    found: list[Found] = []
    found += scan(virtualstore_roots(), "VirtualStore")
    found += scan(install_roots(), "설치경로")
    if extra_roots:
        found += scan([Path(p) for p in extra_roots], "지정경로")
    return sorted(found, key=lambda f: (f.path.name.lower(), -f.mtime.timestamp()))


def group_by_name(found: list[Found]) -> dict[str, list[Found]]:
    """같은 파일명끼리 묶는다. 같은 이름의 사본이 여럿이면 그게 단서다."""
    groups: dict[str, list[Found]] = {}
    for item in found:
        groups.setdefault(item.path.name.lower(), []).append(item)
    return groups


def format_sweep(found: list[Found]) -> str:
    if not found:
        return (
            "관련 파일을 찾지 못했다.\n"
            "  - 윈도우가 아니거나, VirtualStore에 남은 것이 없거나,\n"
            "  - 설치 경로가 예상 밖이다. --root 로 직접 지정해서 다시 훑어보라."
        )

    out = [f"{len(found)}개 파일을 찾았다.", ""]
    groups = group_by_name(found)

    conflicts = {name: items for name, items in groups.items() if len(items) > 1}
    if conflicts:
        out.append("── 같은 이름의 사본이 여럿인 파일 " + "─" * 30)
        out.append("   이 중 VirtualStore 쪽이 더 크거나 더 최신이라면,")
        out.append("   그게 바로 '사라진' 데이터일 가능성이 높다.")
        out.append("")
        for name, items in sorted(conflicts.items()):
            out.append(f"  {name}")
            newest = max(items, key=lambda f: f.mtime)
            largest = max(items, key=lambda f: f.size)
            for item in sorted(items, key=lambda f: -f.mtime.timestamp()):
                marks = []
                if item is newest:
                    marks.append("최신")
                if item is largest and largest.size > min(i.size for i in items):
                    marks.append("최대")
                flag = f"  <- {', '.join(marks)}" if marks else ""
                out.append(f"    {item}{flag}")
            if len({i.digest for i in items}) == 1:
                out.append("    (내용 동일)")
            out.append("")

    out.append("── 전체 목록 " + "─" * 46)
    for item in found:
        out.append(f"  {item}")

    out.append("")
    out.append("다음 할 일: VirtualStore 쪽 사본을 안전한 곳에 복사한 뒤")
    out.append("            sskctl probe 로 내용을 확인하라. 원본은 건드리지 말 것.")
    return "\n".join(out)
