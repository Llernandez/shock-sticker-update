"""sskctl — Shock Sticker 구조 도구 명령줄 진입점."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, backup, probe, rescue


def _read(path: str) -> bytes:
    return Path(path).read_bytes()


def cmd_probe(args: argparse.Namespace) -> int:
    data = _read(args.file)
    report = probe.probe(data, path=args.file)
    print(probe.format_report(report, max_strings=args.limit))
    if args.out:
        Path(args.out).write_text(
            probe.format_report(report, max_strings=10**9), encoding="utf-8"
        )
        print(f"\n전체 리포트를 {args.out} 에 저장했다.")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    regions = probe.byte_diff(_read(args.a), _read(args.b), context=args.context)
    print(probe.format_diff(regions))
    return 0


def cmd_rescue(args: argparse.Namespace) -> int:
    found = rescue.sweep(extra_roots=args.root)
    print(rescue.format_sweep(found))
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    snap, shrank = backup.take_snapshot(
        Path(args.src), Path(args.dest), keep=args.keep
    )
    print(f"세대 생성: {snap.path}")
    print(f"  파일 {snap.file_count}개, {snap.total_bytes:,} 바이트")
    if shrank:
        print("  ** 데이터가 크게 줄었다. 직전 세대를 보호 표시했다. **")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    snaps = backup.list_snapshots(Path(args.dest))
    if not snaps:
        print("세대가 없다.")
        return 0
    for snap in snaps:
        mark = " [보호됨]" if snap.protected else ""
        print(
            f"{snap.path.name}  파일 {snap.file_count:>4}개  "
            f"{snap.total_bytes:>12,} 바이트{mark}"
        )
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    print(f"감시 시작: {args.src}")
    print(f"백업 위치: {args.dest}  (주기 {args.interval}초, 세대 {args.keep}개 유지)")
    print("중단하려면 Ctrl+C.\n")
    try:
        backup.watch(
            Path(args.src), Path(args.dest), interval=args.interval, keep=args.keep
        )
    except KeyboardInterrupt:
        print("\n감시를 멈췄다.")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    snapshot_dir = Path(args.snapshot)
    if not (snapshot_dir / backup.MANIFEST_NAME).is_file():
        print(f"세대가 아니다: {snapshot_dir}", file=sys.stderr)
        return 1
    target = Path(args.target)
    print(f"{snapshot_dir.name} 을(를) {target} 에 복원한다.")
    if not args.yes:
        answer = input("현재 내용을 덮어쓴다. 계속하겠는가? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("취소했다.")
            return 1
    safety = backup.restore(snapshot_dir, target, backup_root=args.dest)
    if safety:
        print(f"복원 전 현재 상태를 {safety.path.name} 으로 남겼다.")
    print("복원 완료.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sskctl",
        description="Shock Sticker 데이터 구조 도구. 원본 실행 파일은 건드리지 않는다.",
    )
    parser.add_argument("--version", action="version", version=f"sskctl {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="파일을 관측해 포맷 단서를 뽑는다")
    p.add_argument("file")
    p.add_argument("--limit", type=int, default=40, help="항목별 출력 개수 상한")
    p.add_argument("--out", help="전체 리포트를 저장할 경로")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("diff", help="두 파일의 바이트 차이를 본다 (통제 실험용)")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--context", type=int, default=8)
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("rescue", help="VirtualStore와 설치 경로에서 잔존 데이터를 찾는다")
    p.add_argument("--root", action="append", help="추가로 훑을 경로 (여러 번 지정 가능)")
    p.set_defaults(func=cmd_rescue)

    p = sub.add_parser("snapshot", help="지금 상태로 세대를 하나 뜬다")
    p.add_argument("src", help="감시할 data 폴더")
    p.add_argument("dest", help="백업을 쌓을 폴더")
    p.add_argument("--keep", type=int, default=30)
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("list", help="쌓인 세대를 나열한다")
    p.add_argument("dest")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("watch", help="변경을 감지해 자동으로 세대를 뜬다")
    p.add_argument("src")
    p.add_argument("dest")
    p.add_argument("--interval", type=float, default=30.0)
    p.add_argument("--keep", type=int, default=30)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("restore", help="세대를 되돌린다")
    p.add_argument("snapshot", help="복원할 세대 폴더")
    p.add_argument("target", help="복원 대상 data 폴더")
    p.add_argument("--dest", help="복원 전 현재 상태를 남길 백업 폴더")
    p.add_argument("--yes", action="store_true", help="확인 없이 진행")
    p.set_defaults(func=cmd_restore)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
