"""바이너리 관측 도구.

.ssk 포맷은 아직 미지수다. 이 모듈은 포맷을 안다고 가정하지 않는다.
파일에서 관측 가능한 사실만 뽑아내고, 해석은 '가설'로만 표시한다.

핵심 질문 세 가지에 답하는 것이 목표다.
  1. 이 파일은 압축/암호화되어 있는가?      -> 엔트로피
  2. 텍스트는 어떤 인코딩으로 들어 있는가?   -> 문자열 스캔
  3. 레코드 경계는 어디인가?                -> 길이 접두사 탐지
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

# Delphi가 TStream.WriteComponent로 객체를 직렬화하면 이 매직으로 시작한다.
# .ssk가 이걸로 시작한다면 포맷 해석은 사실상 끝난 것이나 다름없다.
DELPHI_DFM_MAGIC = b"TPF0"

KNOWN_MAGICS: dict[bytes, str] = {
    b"PK\x03\x04": "ZIP / OOXML",
    b"\xd0\xcf\x11\xe0": "OLE2 복합 문서",
    b"\x1f\x8b": "gzip",
    b"BZh": "bzip2",
    b"\xfd7zXZ": "xz",
    b"SQLite format 3\x00": "SQLite",
    b"TPF0": "Delphi 직렬화 컴포넌트 (DFM binary)",
    b"\xef\xbb\xbf": "UTF-8 BOM",
    b"\xff\xfe": "UTF-16LE BOM",
    b"\xfe\xff": "UTF-16BE BOM",
}

# CP949 트레일 바이트가 취할 수 있는 범위.
_CP949_TRAIL = set(range(0x41, 0x5B)) | set(range(0x61, 0x7B)) | set(range(0x81, 0xFF))


def shannon_entropy(data: bytes) -> float:
    """바이트 단위 섀넌 엔트로피(0~8). 8에 가까우면 압축/암호화를 의심한다."""
    if not data:
        return 0.0
    counts = Counter(data)
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def entropy_profile(data: bytes, window: int = 256) -> list[tuple[int, float]]:
    """윈도우별 엔트로피. 헤더는 낮고 본문만 높다면 부분 압축을 의심한다."""
    return [
        (off, shannon_entropy(data[off : off + window]))
        for off in range(0, len(data), window)
    ]


def hexdump(data: bytes, base: int = 0, limit: int | None = None) -> str:
    """고전적인 16바이트 폭 hexdump. 우측 ASCII 컬럼 포함."""
    body = data if limit is None else data[:limit]
    lines = []
    for off in range(0, len(body), 16):
        chunk = body[off : off + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{base + off:08x}  {hexpart:<47}  |{text}|")
    return "\n".join(lines)


@dataclass
class StringHit:
    offset: int
    encoding: str
    text: str

    def __str__(self) -> str:
        return f"{self.offset:08x}  [{self.encoding}]  {self.text!r}"


def extract_cp949_strings(data: bytes, min_chars: int = 2) -> list[StringHit]:
    """CP949(EUC-KR 확장) 문자열을 훑는다.

    Delphi 2007은 유니코드 이전이라 문자열이 AnsiString(=시스템 코드페이지)로
    저장된다. 한국어 윈도우에서 그 코드페이지는 CP949다.
    """
    hits: list[StringHit] = []
    buf = bytearray()
    start: int | None = None
    i = 0

    def flush() -> None:
        nonlocal buf, start
        if start is not None and buf:
            try:
                text = bytes(buf).decode("cp949")
            except UnicodeDecodeError:
                text = bytes(buf).decode("cp949", errors="replace")
            if len(text) >= min_chars:
                hits.append(StringHit(start, "cp949", text))
        buf = bytearray()
        start = None

    while i < len(data):
        b = data[i]
        if 0x20 <= b < 0x7F:
            if start is None:
                start = i
            buf.append(b)
            i += 1
        elif 0x81 <= b <= 0xFD and i + 1 < len(data) and data[i + 1] in _CP949_TRAIL:
            if start is None:
                start = i
            buf += data[i : i + 2]
            i += 2
        else:
            flush()
            i += 1
    flush()
    return hits


def extract_utf16le_strings(data: bytes, min_chars: int = 2) -> list[StringHit]:
    """UTF-16LE 문자열. 후기 Delphi나 윈도우 API 경유 저장이면 여기 걸린다."""
    hits: list[StringHit] = []
    buf: list[str] = []
    start: int | None = None
    i = 0

    def flush() -> None:
        nonlocal buf, start
        if start is not None and len(buf) >= min_chars:
            hits.append(StringHit(start, "utf-16le", "".join(buf)))
        buf = []
        start = None

    while i + 1 < len(data):
        code = data[i] | (data[i + 1] << 8)
        printable_ascii = 0x20 <= code < 0x7F
        hangul = 0xAC00 <= code <= 0xD7A3 or 0x3130 <= code <= 0x318F
        if printable_ascii or hangul:
            if start is None:
                start = i
            buf.append(chr(code))
            i += 2
        else:
            flush()
            i += 2 if start is None else 1
    flush()
    return hits


@dataclass
class LengthPrefixHit:
    offset: int      # 길이 필드의 위치
    width: int       # 길이 필드의 바이트 수
    length: int      # 읽어낸 길이 값
    text: str        # 그 길이만큼 뒤따르는 바이트를 디코딩한 결과

    @property
    def end(self) -> int:
        return self.offset + self.width + self.length


def _looks_like_text(raw: bytes) -> str | None:
    """CP949로 깔끔하게 디코딩되고 제어문자가 거의 없으면 그 텍스트를 돌려준다."""
    try:
        text = raw.decode("cp949")
    except UnicodeDecodeError:
        return None
    control = sum(1 for ch in text if ord(ch) < 0x20 and ch not in "\r\n\t")
    if control > len(text) * 0.1:
        return None
    return text


def find_length_prefixed_strings(
    data: bytes,
    width: int = 4,
    endian: str = "little",
    min_len: int = 2,
    max_len: int = 8192,
) -> list[LengthPrefixHit]:
    """정수 필드 바로 뒤에 그 길이만큼의 텍스트가 오는 지점을 찾는다.

    Delphi가 문자열을 스트림에 쓸 때 흔한 형태는 셋이다.
        ShortString : 1바이트 길이 + 바이트열
        AnsiString  : 4바이트 길이 + CP949 바이트열
        WideString  : 4바이트 길이(바이트 수) + UTF-16LE

    이 함수의 히트가 규칙적인 간격으로 나온다면, 그 간격이 곧 레코드 구조다.
    포맷 역공학에서 가장 수확이 좋은 한 수다.
    """
    hits: list[LengthPrefixHit] = []
    i = 0
    while i + width <= len(data):
        n = int.from_bytes(data[i : i + width], endian)
        if min_len <= n <= max_len and i + width + n <= len(data):
            text = _looks_like_text(data[i + width : i + width + n])
            if text is not None:
                hits.append(LengthPrefixHit(i, width, n, text))
                i = i + width + n     # 겹치는 히트를 피해 건너뛴다
                continue
        i += 1
    return hits


def stride_histogram(offsets: list[int]) -> Counter:
    """연속한 오프셋 사이의 간격 분포. 지배적인 값이 곧 레코드 크기 후보다."""
    return Counter(b - a for a, b in zip(offsets, offsets[1:]))


def scan_magics(data: bytes) -> list[str]:
    """파일 선두에서 알려진 매직을 찾는다."""
    return [name for magic, name in KNOWN_MAGICS.items() if data.startswith(magic)]


def find_timestamps(data: bytes) -> list[tuple[int, float]]:
    """Delphi TDateTime(8바이트 float) 후보를 찾는다.

    TDateTime은 1899-12-30을 0으로 하는 일수다. 1990~2050년은 대략 32000~55000.
    """
    import struct

    out: list[tuple[int, float]] = []
    for i in range(0, len(data) - 8):
        (val,) = struct.unpack_from("<d", data, i)
        if 32000.0 < val < 55000.0:
            out.append((i, val))
    return out


def byte_diff(a: bytes, b: bytes, context: int = 8) -> list[tuple[int, bytes, bytes]]:
    """두 파일의 차이 구간을 돌려준다.

    2편의 통제 실험이 이걸 쓴다. 메모 하나만 바꾸고 저장한 뒤 diff를 뜨면,
    그 필드가 파일 어디에 있는지 바로 드러난다.
    """
    regions: list[tuple[int, bytes, bytes]] = []
    n = max(len(a), len(b))
    i = 0
    while i < n:
        if a[i : i + 1] == b[i : i + 1]:
            i += 1
            continue
        start = i
        # 동일한 바이트가 context개 연속될 때까지를 하나의 변경 구간으로 본다.
        same = 0
        while i < n and same < context:
            same = same + 1 if a[i : i + 1] == b[i : i + 1] else 0
            i += 1
        end = i - same
        regions.append((start, a[start:end], b[start:end]))
    return regions


@dataclass
class ProbeReport:
    path: str
    size: int
    entropy: float
    magics: list[str] = field(default_factory=list)
    head: str = ""
    tail: str = ""
    cp949: list[StringHit] = field(default_factory=list)
    utf16: list[StringHit] = field(default_factory=list)
    prefixed: dict[int, list[LengthPrefixHit]] = field(default_factory=dict)
    timestamps: list[tuple[int, float]] = field(default_factory=list)


def probe(data: bytes, path: str = "<memory>") -> ProbeReport:
    """파일 하나에 대한 전체 관측 리포트를 만든다."""
    report = ProbeReport(
        path=path,
        size=len(data),
        entropy=shannon_entropy(data),
        magics=scan_magics(data),
        head=hexdump(data, limit=256),
        tail=hexdump(data[-128:], base=max(0, len(data) - 128)) if len(data) > 256 else "",
        cp949=extract_cp949_strings(data),
        utf16=extract_utf16le_strings(data),
        timestamps=find_timestamps(data),
    )
    for width in (1, 2, 4):
        report.prefixed[width] = find_length_prefixed_strings(data, width=width)
    return report


def format_report(r: ProbeReport, max_strings: int = 40) -> str:
    """리포트를 사람이 읽을 수 있는 텍스트로 만든다."""
    out: list[str] = []
    add = out.append

    add(f"파일      : {r.path}")
    add(f"크기      : {r.size:,} 바이트")
    add(f"엔트로피  : {r.entropy:.3f} / 8.000")
    if r.entropy > 7.5:
        add("            -> 7.5 초과. 압축 또는 암호화되었을 가능성이 높다.")
    elif r.entropy < 4.0:
        add("            -> 낮다. 평문 구조체일 가능성이 높다. 반가운 신호다.")
    add(f"매직      : {', '.join(r.magics) if r.magics else '알려진 매직 없음'}")

    add("")
    add("── 선두 256바이트 " + "─" * 40)
    add(r.head)
    if r.tail:
        add("")
        add("── 말미 128바이트 " + "─" * 40)
        add(r.tail)

    for width in (1, 2, 4):
        hits = r.prefixed.get(width, [])
        if not hits:
            continue
        add("")
        add(f"── {width}바이트 길이 접두사 후보: {len(hits)}건 " + "─" * 25)
        strides = stride_histogram([h.offset for h in hits]).most_common(5)
        if strides:
            add(f"   간격 분포(상위 5): {strides}")
        for h in hits[:max_strings]:
            preview = h.text if len(h.text) <= 60 else h.text[:57] + "..."
            add(f"   {h.offset:08x}  len={h.length:<6} {preview!r}")
        if len(hits) > max_strings:
            add(f"   ... 외 {len(hits) - max_strings}건")

    for label, hits in (("CP949", r.cp949), ("UTF-16LE", r.utf16)):
        if not hits:
            continue
        add("")
        add(f"── {label} 문자열: {len(hits)}건 " + "─" * 30)
        for h in hits[:max_strings]:
            preview = h.text if len(h.text) <= 60 else h.text[:57] + "..."
            add(f"   {h.offset:08x}  {preview!r}")
        if len(hits) > max_strings:
            add(f"   ... 외 {len(hits) - max_strings}건")

    if r.timestamps:
        add("")
        add(f"── TDateTime 후보: {len(r.timestamps)}건 " + "─" * 28)
        for off, val in r.timestamps[:20]:
            add(f"   {off:08x}  {val:.6f}  ≈ {_delphi_date(val)}")

    return "\n".join(out)


def _delphi_date(value: float) -> str:
    """TDateTime 값을 읽을 수 있는 날짜로 바꾼다. 기준일은 1899-12-30."""
    from datetime import datetime, timedelta

    return (datetime(1899, 12, 30) + timedelta(days=value)).strftime("%Y-%m-%d %H:%M:%S")


def format_diff(regions: list[tuple[int, bytes, bytes]]) -> str:
    """byte_diff 결과를 읽을 수 있게 출력한다."""
    if not regions:
        return "두 파일이 동일하다."
    out = [f"변경 구간 {len(regions)}개", ""]
    for start, old, new in regions:
        out.append(f"@ 0x{start:08x} ({start})  {len(old)} -> {len(new)} 바이트")
        out.append("  이전: " + (hexdump(old, base=start) or "  (없음)"))
        out.append("  이후: " + (hexdump(new, base=start) or "  (없음)"))
        for label, raw in (("이전", old), ("이후", new)):
            text = _looks_like_text(raw)
            if text:
                out.append(f"  {label} 텍스트: {text!r}")
        out.append("")
    return "\n".join(out)
