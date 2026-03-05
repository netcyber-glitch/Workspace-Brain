# EXE/포터블/배포 메모 (lite/full)

이 문서는 “지인에게 전달 가능한 형태(EXE)”를 만들기 전에 결정해야 할 사항과, 권장 방향을 정리합니다.

## 목표(사용자 선택)
1) **포터블(Portable) 모드**
- 실행 파일(또는 폴더) 옆에 `data/`를 만들고 그 안에 인덱스(DB/Chroma/스냅샷)를 저장
- 설치 없이 “폴더 통째로 복사”해서 사용 가능

2) **lite/full 2가지 배포**
- **lite**: FTS(키워드) 중심. 벡터/임베딩 없이도 실행 가능(가볍고 설치가 쉬움)
- **full**: Vector/Hybrid 포함(Chroma + 임베딩 모델 포함). 품질은 좋지만 용량/빌드 시간이 커질 수 있음

## 현재 상태(소스 실행 기준)
- 기본 저장소 경로가 `data/` 아래로 잡혀 있어 “소스 폴더 단위”로는 이미 포터블에 가까운 구조입니다.
- EXE로 바꿀 때는 “실행 위치”가 바뀌므로, **데이터 루트(=portable data 폴더)를 어떻게 찾을지**를 코드에서 확정해야 합니다.

## 결정이 필요한 핵심 3가지
### 1) 데이터 루트 선택 규칙
포터블 모드에서는 보통 아래 중 하나를 씁니다.
- (A) `workspace_brain.exe`가 있는 폴더 기준 `.\data\`
- (B) 사용자의 AppData 기준(예: `%LOCALAPPDATA%/Workspace_Brain/`)  ← 포터블이 아니라 설치형에 가까움

요청 방향은 (A)입니다.

### 2) lite/full의 기능 차이(권장)
- lite
  - 스캔/FTS/미리보기/태그/연관문서(링크/작업흐름/버전 체인)까지는 동작
  - Vector/Hybrid는 UI에서 숨기거나(또는 선택 시 “설치 필요” 안내) 처리
- full
  - Vector/Hybrid + 벡터 인덱싱 포함
  - 버전 체인의 “내용 유사도”까지 활성화 가능(임베딩 필요)

### 3) 의존성 분리 방법
권장:
- `requirements-lite.txt` / `requirements-full.txt` 분리 또는
- `pyproject.toml`로 extras(`workspace-brain[lite]`, `workspace-brain[full]`) 구성

## PyInstaller 빌드(예정)
Windows에서 보통 PyInstaller로 “one-folder” 빌드를 추천합니다.
- onefile은 실행 시 임시 폴더 압축 해제가 들어가서, 데이터 경로/속도/안정성 관리가 더 까다로울 수 있습니다.

예시(방향만, 확정 후 스크립트화 권장):
- `pyinstaller --noconfirm --clean --name Workspace-Brain --noconsole workspace_brain_gui.py`

## GPL-3.0 배포 메모(핵심만)
- EXE로 지인에게 전달하는 것도 “배포”에 해당합니다.
- GPL-3.0은 배포 시 소스(수정 포함) 제공 의무가 생기는데, GitHub에 소스가 공개되어 있고 해당 버전을 가리키면 충족하기 쉬운 편입니다.

