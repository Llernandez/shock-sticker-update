"""관측 도구 검증.

포맷을 모른다는 것과, 도구가 작동하지 않는다는 것은 다르다.
Delphi 관용구로 만든 합성 파일에서 probe가 구조를 짚어내는지 확인한다.
"""

from __future__ import annotations

import struct

from ssk import probe

from conftest import SAMPLE_NOTES, make_ssk


def test_entropy_range(sample_ssk):
    value = probe.shannon_entropy(sample_ssk)
    assert 0.0 < value < 8.0
    # 평문 구조체다. 압축된 것처럼 보이면 안 된다.
    assert value < 7.0


def test_entropy_of_random_data_is_high():
    import os

    assert probe.shannon_entropy(os.urandom(4096)) > 7.5


def test_hexdump_shape(sample_ssk):
    dump = probe.hexdump(sample_ssk, limit=32)
    lines = dump.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("00000000  53 53 4b 01")   # "SSK\x01"
    assert lines[0].endswith("|SSK.............|")   # 한 줄 16바이트


def test_cp949_strings_recover_korean(sample_ssk):
    texts = [hit.text for hit in probe.extract_cp949_strings(sample_ssk)]
    joined = " ".join(texts)
    assert "회의 준비" in joined
    assert "우유, 계란, 빵" in joined


def test_length_prefix_detection_finds_every_note(sample_ssk):
    """이 프로젝트에서 가장 중요한 한 수. 레코드 경계를 짚어내야 한다."""
    hits = probe.find_length_prefixed_strings(sample_ssk, width=4)
    found = {hit.text for hit in hits}
    for title, body in SAMPLE_NOTES:
        assert title in found, f"제목을 놓쳤다: {title}"
        assert body in found, f"본문을 놓쳤다: {body}"


def test_length_prefix_width_matters():
    """1바이트 길이(ShortString)로 저장된 파일도 잡아내야 한다."""
    data = make_ssk(SAMPLE_NOTES, width=1)
    hits = probe.find_length_prefixed_strings(data, width=1)
    assert {h.text for h in hits} >= {title for title, _ in SAMPLE_NOTES}


def test_timestamp_detection(sample_ssk):
    stamps = probe.find_timestamps(sample_ssk)
    assert stamps, "TDateTime을 하나도 못 찾았다"
    # 2024년 값이 들어 있어야 한다.
    assert any("2024-03" in probe._delphi_date(v) for _, v in stamps)


def test_magic_scan():
    assert probe.scan_magics(b"TPF0rest") == ["Delphi 직렬화 컴포넌트 (DFM binary)"]
    assert probe.scan_magics(b"SSK\x01") == []


def test_byte_diff_isolates_the_changed_field():
    """통제 실험의 핵심. 메모 하나만 바꾸면 그 구간만 나와야 한다."""
    before = make_ssk(SAMPLE_NOTES)
    changed = list(SAMPLE_NOTES)
    changed[1] = ("장보기", "우유, 계란, 빵, 사과")
    after = make_ssk(changed)

    regions = probe.byte_diff(before, after)
    assert regions, "차이를 못 찾았다"
    # 첫 변경 지점은 두 번째 메모 이후여야 한다. 첫 메모는 그대로다.
    first_change = regions[0][0]
    assert first_change > len(SAMPLE_NOTES[0][0].encode("cp949"))


def test_byte_diff_identical_files(sample_ssk):
    assert probe.byte_diff(sample_ssk, sample_ssk) == []


def test_stride_histogram():
    counts = probe.stride_histogram([0, 10, 20, 30, 45])
    assert counts.most_common(1)[0] == (10, 3)


def test_report_renders(sample_ssk):
    report = probe.probe(sample_ssk, path="sample.ssk")
    text = probe.format_report(report)
    assert "sample.ssk" in text
    assert "엔트로피" in text
    assert "회의 준비" in text


def test_utf16_strings():
    data = b"\x00\x00" + "안녕하세요 world".encode("utf-16le") + b"\x00\x00"
    hits = probe.extract_utf16le_strings(data)
    assert any("안녕하세요" in h.text for h in hits)


def test_looks_like_text_rejects_binary():
    assert probe._looks_like_text(struct.pack("<8d", *range(8))) is None
    assert probe._looks_like_text("한글".encode("cp949")) == "한글"
