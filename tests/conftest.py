"""합성 샘플 생성기.

실제 .ssk 파일이 없어도 관측 도구를 검증할 수 있어야 한다.
Delphi가 쓸 법한 형태로 파일을 만들어, probe가 그 구조를 실제로 짚어내는지 확인한다.

중요: 여기서 만든 것은 .ssk의 '진짜' 구조가 아니다. Delphi 관용구를 흉내 낸 것일 뿐이다.
probe가 그런 관용구를 짚어낼 능력이 있는지만 검증한다.
"""

from __future__ import annotations

import struct
from datetime import datetime

import pytest

DELPHI_EPOCH = datetime(1899, 12, 30)


def to_tdatetime(when: datetime) -> float:
    """파이썬 datetime을 Delphi TDateTime(일 단위 실수)으로."""
    return (when - DELPHI_EPOCH).total_seconds() / 86400.0


def ansi_string(text: str, width: int = 4) -> bytes:
    """Delphi AnsiString 직렬화 흉내: 길이 접두사 + CP949 바이트열."""
    raw = text.encode("cp949")
    return len(raw).to_bytes(width, "little") + raw


def make_ssk(notes: list[tuple[str, str]], width: int = 4) -> bytes:
    """메모 목록으로 Delphi풍 데이터 파일을 만든다."""
    out = bytearray(b"SSK\x01")
    out += len(notes).to_bytes(4, "little")
    for index, (title, body) in enumerate(notes):
        out += ansi_string(title, width)
        out += ansi_string(body, width)
        out += struct.pack("<d", to_tdatetime(datetime(2024, 3, 1 + index, 9, 30)))
        out += struct.pack("<HH", 100 + index * 20, 200 + index * 20)   # 창 좌표
    return bytes(out)


SAMPLE_NOTES = [
    ("회의 준비", "월요일 10시 회의실 예약하고 자료 인쇄"),
    ("장보기", "우유, 계란, 빵"),
    ("Deploy note", "check the staging build before release"),
]


@pytest.fixture
def sample_ssk() -> bytes:
    return make_ssk(SAMPLE_NOTES)


@pytest.fixture
def data_dir(tmp_path):
    """.ssk 파일 몇 개가 든 data 폴더."""
    d = tmp_path / "data"
    d.mkdir()
    (d / "notes.ssk").write_bytes(make_ssk(SAMPLE_NOTES))
    (d / "config.ini").write_text("[main]\nautosave=1\n", encoding="utf-8")
    return d
