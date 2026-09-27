"""백업 검증.

가장 중요한 시험은 마지막 두 개다. 데이터가 사라진 뒤에도
사라지기 전의 세대가 남아 있어야 한다. 그게 이 모듈의 존재 이유다.
"""

from __future__ import annotations

import os

import pytest

from ssk import backup


def test_atomic_write_replaces_content(tmp_path):
    target = tmp_path / "notes.ssk"
    target.write_bytes(b"old")
    backup.atomic_write(target, b"new content")
    assert target.read_bytes() == b"new content"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "notes.ssk"
    backup.atomic_write(target, b"data")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "notes.ssk"]
    assert leftovers == []


def test_scan_tree_fingerprint_changes_with_content(data_dir):
    before = backup.scan_tree(data_dir).fingerprint
    (data_dir / "notes.ssk").write_bytes(b"different")
    after = backup.scan_tree(data_dir).fingerprint
    assert before != after


def test_scan_tree_fingerprint_stable(data_dir):
    assert backup.scan_tree(data_dir).fingerprint == backup.scan_tree(data_dir).fingerprint


def test_snapshot_copies_every_file(data_dir, tmp_path):
    dest = tmp_path / "backup"
    snap, shrank = backup.take_snapshot(data_dir, dest)
    assert not shrank
    assert (snap.path / "notes.ssk").read_bytes() == (data_dir / "notes.ssk").read_bytes()
    assert (snap.path / "config.ini").exists()
    assert snap.file_count == 2


def test_list_snapshots_is_chronological(data_dir, tmp_path):
    dest = tmp_path / "backup"
    for i in range(3):
        (data_dir / "notes.ssk").write_bytes(b"x" * (i + 1) * 100)
        backup.take_snapshot(data_dir, dest)
    snaps = backup.list_snapshots(dest)
    assert len(snaps) == 3
    assert [s.taken_at for s in snaps] == sorted(s.taken_at for s in snaps)


def test_prune_keeps_the_newest(data_dir, tmp_path):
    dest = tmp_path / "backup"
    for i in range(5):
        (data_dir / "notes.ssk").write_bytes(b"x" * (i + 1) * 100)
        backup.take_snapshot(data_dir, dest, keep=3)
    snaps = backup.list_snapshots(dest)
    assert len(snaps) == 3
    # 남은 세 개가 가장 최근 것들이어야 한다.
    assert snaps[-1].total_bytes > snaps[0].total_bytes


def test_shrink_detection_fires(data_dir, tmp_path):
    dest = tmp_path / "backup"
    (data_dir / "notes.ssk").write_bytes(b"x" * 10_000)
    backup.take_snapshot(data_dir, dest)

    # 사고 발생. 메모가 날아갔다.
    (data_dir / "notes.ssk").write_bytes(b"")
    _, shrank = backup.take_snapshot(data_dir, dest)
    assert shrank


def test_shrink_protects_the_previous_generation(data_dir, tmp_path):
    """백업이 사고를 덮어쓰지 않는가 — 이 모듈이 존재하는 이유."""
    dest = tmp_path / "backup"
    (data_dir / "notes.ssk").write_bytes("소중한 메모".encode("cp949") * 500)
    good, _ = backup.take_snapshot(data_dir, dest, keep=2)
    good_bytes = (good.path / "notes.ssk").read_bytes()

    # 사고 이후 세대가 여러 번 쌓여도 keep=2를 넘겨 밀어내지 못해야 한다.
    for i in range(5):
        (data_dir / "notes.ssk").write_bytes(b"")
        (data_dir / f"junk{i}.tmp").write_text("")
        backup.take_snapshot(data_dir, dest, keep=2)

    survivors = backup.list_snapshots(dest)
    protected = [s for s in survivors if s.protected]
    assert protected, "보호된 세대가 없다 — 좋은 데이터가 밀려났다"
    assert (protected[0].path / "notes.ssk").read_bytes() == good_bytes


def test_protected_snapshot_survives_prune(data_dir, tmp_path):
    dest = tmp_path / "backup"
    snap, _ = backup.take_snapshot(data_dir, dest)
    (snap.path / backup.PROTECTED_MARKER).touch()
    for i in range(10):
        (data_dir / "notes.ssk").write_bytes(b"y" * (i + 1))
        backup.take_snapshot(data_dir, dest, keep=1)
    assert snap.path.exists()


def test_rapid_snapshots_never_delete_the_newest(data_dir, tmp_path):
    """회귀 시험.

    세대 이름의 순번을 '폴더가 이미 있는가'로 정했더니, 보존 정책이 같은 초의 앞선
    세대를 지운 뒤 다음 세대가 그 이름을 재사용했다. 그러면 방금 만든 세대가 정렬상
    가장 오래된 것이 되어 즉시 삭제된다. 백업이 아무것도 남기지 않는다.
    """
    dest = tmp_path / "backup"
    for i in range(20):
        (data_dir / "notes.ssk").write_bytes(b"x" * (i + 1) * 10)
        snap, _ = backup.take_snapshot(data_dir, dest, keep=2)
        assert snap.path.is_dir(), "방금 만든 세대가 사라졌다"
        assert backup.list_snapshots(dest)[-1].path == snap.path


def test_snapshot_names_are_monotonic(data_dir, tmp_path):
    dest = tmp_path / "backup"
    order = []
    for i in range(10):
        (data_dir / "notes.ssk").write_bytes(b"x" * (i + 1))
        snap, _ = backup.take_snapshot(data_dir, dest, keep=100)
        order.append(snap.sort_key)
    assert order == sorted(order)
    assert len(set(order)) == len(order), "같은 정렬 키가 두 번 나왔다"


def test_prune_gives_up_rather_than_delete_protected(data_dir, tmp_path):
    """보존 개수는 상한이 아니라 목표다. 지울 게 없으면 그냥 넘어가야 한다."""
    dest = tmp_path / "backup"
    for i in range(4):
        (data_dir / "notes.ssk").write_bytes(b"x" * (i + 1) * 100)
        snap, _ = backup.take_snapshot(data_dir, dest, keep=100)
        (snap.path / backup.PROTECTED_MARKER).touch()

    backup.prune(dest, keep=1)
    assert len(backup.list_snapshots(dest)) == 4


def test_old_style_snapshot_names_still_load(data_dir, tmp_path):
    """밀리초 없는 이름으로 만들어진 기존 백업도 계속 읽혀야 한다."""
    dest = tmp_path / "backup"
    snap, _ = backup.take_snapshot(data_dir, dest)
    legacy = dest / "20240101-120000"
    snap.path.rename(legacy)
    loaded = backup.list_snapshots(dest)
    assert len(loaded) == 1
    assert loaded[0].sequence == 0


def test_restore_brings_content_back(data_dir, tmp_path):
    dest = tmp_path / "backup"
    original = (data_dir / "notes.ssk").read_bytes()
    snap, _ = backup.take_snapshot(data_dir, dest)

    (data_dir / "notes.ssk").write_bytes(b"corrupted")
    backup.restore(snap.path, data_dir)
    assert (data_dir / "notes.ssk").read_bytes() == original


def test_restore_saves_current_state_first(data_dir, tmp_path):
    dest = tmp_path / "backup"
    snap, _ = backup.take_snapshot(data_dir, dest)
    (data_dir / "notes.ssk").write_bytes(b"corrupted but maybe wanted")

    safety = backup.restore(snap.path, data_dir, backup_root=dest)
    assert safety is not None
    assert (safety.path / "notes.ssk").read_bytes() == b"corrupted but maybe wanted"


def test_restore_skips_bookkeeping_files(data_dir, tmp_path):
    dest = tmp_path / "backup"
    snap, _ = backup.take_snapshot(data_dir, dest)
    target = tmp_path / "restored"
    backup.restore(snap.path, target)
    assert not (target / backup.MANIFEST_NAME).exists()


@pytest.mark.skipif(os.name == "nt", reason="윈도우는 디렉터리 fsync를 지원하지 않는다")
def test_atomic_write_survives_missing_dir_fsync(tmp_path):
    # 디렉터리 fsync 실패가 쓰기 자체를 깨뜨리면 안 된다.
    target = tmp_path / "sub" / "notes.ssk"
    target.parent.mkdir()
    backup.atomic_write(target, b"ok")
    assert target.read_bytes() == b"ok"
