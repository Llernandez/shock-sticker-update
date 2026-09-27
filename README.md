# Shock Sticker Rescue Kit

버려진 스티커 메모 프로그램 Shock Sticker의 데이터를 바깥에서 지키는 도구 모음입니다.

Shock Sticker는 Delphi 2007로 빌드된 한국산 메모 프로그램입니다. 개발이 10년 넘게
중단되었고 바이너리는 패킹되어 있어 고칠 수 없습니다. 하지만 아직 쓰는 사람이 있고,
두 가지 고질적인 문제가 있습니다.

- **"잘못된 포맷" 에러** — 메모를 불러오지 못한다
- **데이터 유실** — 메모가 사라지고 돌아오지 않는다

이 리포는 원본을 고치지 않습니다. 원본 실행 파일과 설치 폴더에는 **어떤 쓰기도 하지
않습니다.** 대신 데이터 파일을 관측하고, 사라진 것을 찾아내고, 다시 잃지 않게 합니다.

> ⚠️ 이 리포에는 Shock Sticker 자체가 들어 있지 않습니다. 도구만 있습니다.
> 원본 프로그램과 설치 파일은 저작권이 있으며 재배포하지 않습니다.

---

## 지금 할 수 있는 것

### 1. 사라진 메모 찾기

윈도우 UAC 가상화 때문에, `C:\Program Files (x86)` 하위에 설치된 프로그램의 저장이
조용히 `%LOCALAPPDATA%\VirtualStore\...`로 우회되는 일이 있습니다. 프로그램은 저장에
성공했다고 믿지만, 읽을 때는 원래 경로를 봅니다. **사라졌다고 생각한 메모가 아직
거기 남아 있을 수 있습니다.**

```console
$ sskctl rescue
```

같은 이름의 사본이 여러 곳에서 나오면 크기와 수정 시각을 나란히 보여줍니다.
VirtualStore 쪽이 더 크거나 더 최신이면, 그게 잃어버린 데이터입니다.

### 2. 데이터 파일 관측하기

`.ssk` 포맷은 아직 문서화되어 있지 않습니다. 역공학 중입니다.

```console
$ sskctl probe data/notes.ssk
$ sskctl probe data/notes.ssk --out report.txt     # 전체 리포트 저장
```

엔트로피, 알려진 매직, hexdump, CP949/UTF-16 문자열, Delphi 길이 접두사 후보,
`TDateTime` 후보를 뽑아냅니다.

### 3. 통제 실험

메모를 하나만 바꾸고 저장한 뒤 이전 파일과 비교하면, 그 필드가 파일 어디에 있는지
바로 드러납니다. 포맷 역공학에서 가장 수확이 좋은 방법입니다.

```console
$ sskctl diff before.ssk after.ssk
```

### 4. 다시는 잃지 않기

포맷을 몰라도 백업은 할 수 있습니다. 바이트를 있는 그대로 복사합니다.

```console
$ sskctl snapshot "E:\ShockSticker\data" "E:\ShockSticker\_backup"
$ sskctl watch    "E:\ShockSticker\data" "E:\ShockSticker\_backup" --interval 30
$ sskctl list     "E:\ShockSticker\_backup"
$ sskctl restore  "E:\ShockSticker\_backup\20260927-143012" "E:\ShockSticker\data"
```

`watch`는 변경을 감지할 때마다 세대를 남깁니다. 한 가지 안전장치가 있습니다 —
**데이터가 갑자기 절반 이하로 줄어들면 직전 세대를 보호 표시하고, 보존 정책이 그
세대를 지우지 못하게 합니다.** 데이터 유실 사고에서 백업이 가장 흔하게 실패하는
지점이 사고 직후의 텅 빈 상태를 성실하게 백업하고 멀쩡하던 세대를 밀어내는 것이기
때문입니다.

---

## 아직 없는 것

포맷을 확정하기 전에는 만들 수 없습니다. 추측으로 파서를 쓰지 않습니다.

- `.ssk` 파서와 무결성 검사기
- 손상 파일 복구
- `.ssk` → Markdown 변환기

진행 상황은 [`docs/format-spec.md`](docs/format-spec.md)에, 기각된 가설은
[`docs/deadends.md`](docs/deadends.md)에 기록합니다.

---

## 설치

의존성이 없습니다. 표준 라이브러리만 씁니다.

```console
$ git clone https://github.com/Llernandez/shock-sticker-update
$ cd shock-sticker-update
$ pip install -e .
$ sskctl --help
```

파이썬 없이 쓰려면 단일 실행 파일로 빌드하세요.

```console
$ pip install pyinstaller
$ pyinstaller --onefile --name sskctl -c ssk/cli.py
```

---

## 왜 이걸 만드나

자세한 이야기는 연재 글에서 다룹니다. 각 편이 이 리포의 작업 단계 하나와 대응합니다.

| 편 | 제목 | 산출물 |
|----|------|--------|
| 0 | 사라진 메모를 찾아서 | `ssk/rescue.py` |
| 1 | 봉인된 상자 — 패킹된 Delphi 2007 앞에서 | (정찰) |
| 2 | 바이트를 읽다 — `.ssk` 포맷 역공학 | `ssk/probe.py` |
| 3 | 관대한 파서 — 깨진 파일도 읽는 법 | `ssk/reader.py` |
| 4 | 범인 찾기 — 데이터는 왜 사라졌는가 | `ssk/repair.py` |
| 5 | 다시는 잃지 않게 — 세대 백업 | `ssk/backup.py` |
| 6 | 탈출구 — Markdown으로 꺼내기 | `ssk/export/` |
| 7 | 버려진 것을 고치는 일에 대하여 | v1.0 |

설계 문서: [`docs/specs/2026-09-27-design.md`](docs/specs/2026-09-27-design.md)

---

## 기여

`.ssk` 파일을 가지고 계신다면 큰 도움이 됩니다. 다만 **실제 업무 내용이나 개인정보가
담긴 파일은 올리지 마세요.** 새 메모 몇 개로 만든 합성 샘플이면 충분하고, 오히려 더
유용합니다.

## 라이선스

MIT. 이 리포의 도구 코드에만 적용됩니다.
