"""세대 백업.

이 모듈은 .ssk 포맷을 전혀 모른다. 알 필요도 없다.
바이트를 있는 그대로 복사하고, 세대를 남기고, 함부로 지우지 않는다.

설계상 가장 중요한 결정은 '의심스러운 축소'를 다루는 방식이다.
데이터가 사라지는 사고에서 백업이 가장 흔하게 실패하는 지점이,
사고 직후의 텅 빈 상태를 성실하게 백업하고 멀쩡하던 세대를 밀어내는 것이다.
그래서 여기서는 데이터가 크게 줄어든 것이 감지되면 직전 세대를 보호 표시하고
보존 정책이 그 세대를 건드리지 못하게 한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SNAPSHOT_FORMAT = "%Y%m%d-%H%M%S"
MANIFEST_NAME = "manifest.json"
PROTECTED_MARKER = ".protected"

# 같은 초에 두 번 뜨면 "20260927-143012-1" 처럼 접미사가 붙는다.
# 이름 파싱이 이걸 못 받아내면 그 세대가 목록에서 통째로 사라지고,
# 보존 정책도 복원도 그 세대를 보지 못한다.
SNAPSHOT_NAME_RE = re.compile(r"^(\d{8}-\d{6})(?:-(\d+))?$")

# 전체 바이트 수가 직전 세대의 이 비율 밑으로 떨어지면 의심스러운 축소로 본다.
SHRINK_THRESHOLD = 0.5


def atomic_write(path: Path, data: bytes) -> None:
    """같은 디렉터리에 임시 파일로 쓰고 fsync한 뒤 원자적으로 교체한다.

    Shock Sticker 자체가 하지 않는(것으로 의심되는) 바로 그 일이다.
    쓰기 도중 전원이 끊겨도 원본은 온전하거나 완전히 교체되거나 둘 중 하나다.
    절반만 남는 상태가 생기지 않는다.
    """
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    # 디렉터리 엔트리 자체도 내려써야 교체가 확실히 남는다.
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    except OSError:
        pass          # 윈도우는 디렉터리 fsync를 지원하지 않는다. 치명적이지 않다.
    finally:
        os.close(dir_fd)


def file_digest(path: Path, chunk: int = 1 << 16) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


@dataclass
class TreeState:
    """디렉터리 한 그루의 상태. 변경 감지의 기준이 된다."""

    files: dict[str, tuple[int, str]] = field(default_factory=dict)  # 상대경로 -> (크기, 해시)

    @property
    def total_bytes(self) -> int:
        return sum(size for size, _ in self.files.values())

    @property
    def fingerprint(self) -> str:
        blob = "\n".join(f"{p}:{s}:{d}" for p, (s, d) in sorted(self.files.items()))
        return hashlib.sha256(blob.encode()).hexdigest()


def scan_tree(root: Path, patterns: tuple[str, ...] = ("*",)) -> TreeState:
    """디렉터리를 훑어 상태를 만든다."""
    root = Path(root)
    state = TreeState()
    for pattern in patterns:
        for path in sorted(root.rglob(pattern)):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel.startswith("."):
                continue
            state.files[rel] = (path.stat().st_size, file_digest(path))
    return state


def parse_snapshot_name(name: str) -> tuple[datetime, int] | None:
    """세대 폴더 이름을 (시각, 같은 초 안의 순번)으로 푼다. 아니면 None."""
    match = SNAPSHOT_NAME_RE.match(name)
    if not match:
        return None
    try:
        stamp = datetime.strptime(match.group(1), SNAPSHOT_FORMAT)
    except ValueError:
        return None
    return stamp, int(match.group(2) or 0)


def _next_snapshot_dir(backup_root: Path) -> Path:
    """다음 세대 폴더 경로. 초 안에서도 순번이 단조 증가해야 한다.

    순번을 '그 폴더가 이미 있는가'로 정하면 안 된다. 보존 정책이 같은 초의 앞선 세대를
    지우고 나면 다음 세대가 그 이름을 재사용하고, 방금 만든 세대가 정렬상 가장 오래된
    것이 되어 바로 다음 정리에서 삭제된다. 그래서 시계에서 밀리초를 가져다 쓴다.
    """
    now = datetime.now()
    stamp = now.strftime(SNAPSHOT_FORMAT)
    sequence = now.microsecond // 1000
    dest = backup_root / f"{stamp}-{sequence:03d}"
    while dest.exists():
        sequence += 1
        dest = backup_root / f"{stamp}-{sequence:03d}"
    return dest


@dataclass
class Snapshot:
    path: Path
    taken_at: datetime
    sequence: int
    total_bytes: int
    file_count: int
    protected: bool

    @property
    def sort_key(self) -> tuple[datetime, int]:
        return self.taken_at, self.sequence

    @classmethod
    def load(cls, path: Path) -> "Snapshot | None":
        parsed = parse_snapshot_name(path.name)
        if parsed is None:
            return None
        manifest = path / MANIFEST_NAME
        if not manifest.is_file():
            return None
        try:
            meta = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        taken_at, sequence = parsed
        return cls(
            path=path,
            taken_at=taken_at,
            sequence=sequence,
            total_bytes=meta.get("total_bytes", 0),
            file_count=len(meta.get("files", {})),
            protected=(path / PROTECTED_MARKER).exists(),
        )


def list_snapshots(backup_root: Path) -> list[Snapshot]:
    """오래된 것부터 정렬된 세대 목록."""
    backup_root = Path(backup_root)
    if not backup_root.is_dir():
        return []
    found = [
        snap
        for child in backup_root.iterdir()
        if child.is_dir() and (snap := Snapshot.load(child)) is not None
    ]
    return sorted(found, key=lambda s: s.sort_key)


def take_snapshot(
    src: Path,
    backup_root: Path,
    patterns: tuple[str, ...] = ("*",),
    keep: int = 30,
) -> tuple[Snapshot, bool]:
    """세대 하나를 뜬다.

    Returns:
        (생성된 세대, 의심스러운 축소가 감지되었는지)
    """
    src, backup_root = Path(src), Path(backup_root)
    state = scan_tree(src, patterns)

    previous = list_snapshots(backup_root)
    shrank = bool(
        previous
        and previous[-1].total_bytes > 0
        and state.total_bytes < previous[-1].total_bytes * SHRINK_THRESHOLD
    )

    dest = _next_snapshot_dir(backup_root)
    dest.mkdir(parents=True)

    for rel in state.files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)

    manifest = {
        "source": str(src),
        "taken_at": datetime.now().isoformat(timespec="seconds"),
        "total_bytes": state.total_bytes,
        "fingerprint": state.fingerprint,
        "shrink_detected": shrank,
        "files": {rel: {"size": s, "sha256": d} for rel, (s, d) in state.files.items()},
    }
    atomic_write(dest / MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2).encode())

    if shrank and previous:
        # 축소 직전 세대를 보호한다. 이게 이 모듈의 존재 이유다.
        (previous[-1].path / PROTECTED_MARKER).touch()
        previous[-1].protected = True

    prune(backup_root, keep=keep)
    snap = Snapshot.load(dest)
    assert snap is not None
    return snap, shrank


def prune(backup_root: Path, keep: int) -> list[Path]:
    """보존 개수를 넘긴 오래된 세대를 정리한다.

    두 가지는 어떤 경우에도 지우지 않는다.
      - 보호 표시된 세대 (데이터 축소 직전의 마지막 정상 상태)
      - 가장 최근 세대

    보호된 세대가 많아 보존 개수를 넘더라도, 지울 수 있는 것이 없으면 그냥 넘긴다.
    보존 개수는 상한이 아니라 목표다. 백업 도구가 지우는 쪽으로 무리하면 안 된다.
    """
    snapshots = list_snapshots(backup_root)
    newest = snapshots[-1] if snapshots else None
    removable = [s for s in snapshots if not s.protected and s is not newest]

    excess = len(snapshots) - max(keep, 1)
    removed: list[Path] = []
    for snap in removable:
        if excess <= 0:
            break
        shutil.rmtree(snap.path)
        removed.append(snap.path)
        excess -= 1
    return removed


def restore(snapshot_dir: Path, target: Path, backup_root: Path | None = None) -> Snapshot | None:
    """세대를 되돌린다. 되돌리기 전에 현재 상태를 반드시 한 번 더 뜬다.

    복원 자체가 사고가 되는 경우를 막기 위한 장치다.
    """
    snapshot_dir, target = Path(snapshot_dir), Path(target)
    safety: Snapshot | None = None
    if backup_root and target.is_dir():
        safety, _ = take_snapshot(target, Path(backup_root), keep=10_000)

    target.mkdir(parents=True, exist_ok=True)
    for path in snapshot_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(snapshot_dir).as_posix()
        if rel in (MANIFEST_NAME, PROTECTED_MARKER):
            continue
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    return safety


def watch(
    src: Path,
    backup_root: Path,
    interval: float = 30.0,
    patterns: tuple[str, ...] = ("*",),
    keep: int = 30,
    on_event=print,
) -> None:
    """변경을 폴링으로 감지해 세대를 뜬다.

    외부 의존성을 쓰지 않는다. 메모 몇 개짜리 디렉터리를 30초마다 해싱하는 비용은
    무시할 수 있고, 의존성 없는 쪽이 단일 exe 배포에 압도적으로 유리하다.
    """
    src, backup_root = Path(src), Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)

    last = scan_tree(src, patterns).fingerprint if src.is_dir() else None
    existing = list_snapshots(backup_root)
    if not existing and src.is_dir():
        snap, _ = take_snapshot(src, backup_root, patterns, keep)
        on_event(f"[{snap.taken_at:%H:%M:%S}] 최초 세대 생성: {snap.path.name}")

    while True:
        time.sleep(interval)
        if not src.is_dir():
            on_event(f"경고: 원본 디렉터리가 사라졌다 — {src}")
            continue
        current = scan_tree(src, patterns)
        if current.fingerprint == last:
            continue
        last = current.fingerprint
        snap, shrank = take_snapshot(src, backup_root, patterns, keep)
        note = "  ** 데이터 축소 감지 — 직전 세대를 보호했다 **" if shrank else ""
        on_event(
            f"[{snap.taken_at:%H:%M:%S}] 세대 {snap.path.name} "
            f"({snap.file_count}개 파일, {snap.total_bytes:,} 바이트){note}"
        )
