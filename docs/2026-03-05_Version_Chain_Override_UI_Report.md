# Workspace Brain — 버전 체인 오버라이드(UI) 추가 보고서

작성: codex 5.2 (2026-03-05 KST)

## 결론
- 연관문서 탭에서 **버전 체인 오버라이드(pin/exclude/include/clear)** 를 바로 저장할 수 있게 UI 버튼을 추가했습니다.
- 오버라이드가 있으면 연관문서 항목의 “근거/점수” 컬럼에 **`[PIN:...]` / `[EXCLUDE]` 배지**로 표시됩니다.
- 오버라이드는 즉시 DB에 저장되지만, **`version_chains` 반영은 별도로 “버전 체인 재구축” 실행이 필요**합니다(인덱싱 탭에서 실행).

## 변경 사항
- UI(연관문서 탭)
  - `D:\Workspace_Brain\src\ui\main_window.py`
    - 버튼 추가: `체인 Pin…`, `체인 제외`, `제외 해제`, `오버라이드 삭제`, `인덱싱…`
    - 선택한 연관문서에 대해 오버라이드 저장(백그라운드 스레드)
    - 오버라이드 배지 표시(`[PIN:...]`, `[EXCLUDE]`)
    - `인덱싱…` 버튼: 환경설정 다이얼로그를 **인덱싱 탭으로 바로 열고**, 프로젝트가 있으면 자동 선택 시도
- 백엔드(DB 쓰기)
  - `D:\Workspace_Brain\src\ui\backend.py`
    - `version_chain_overrides` 테이블에 대한 조회/업서트/삭제 유틸 추가
      - `pin_version_chain_doc`, `exclude_from_version_chains`, `include_in_version_chains`, `clear_version_chain_override`
      - `get_version_chain_override`, `get_version_chain_overrides`

## 사용 방법
1) 문서 선택 → 우측 탭 `연관문서`에서 대상 문서를 클릭
2) 아래 버튼으로 오버라이드 저장
   - `체인 Pin…`: `manual_chain_key` 입력(같은 키는 같은 체인으로 강제 묶기)
   - `체인 제외`: 체인 빌드에서 제외(자동/수동 모두 제외)
   - `제외 해제`: 제외 플래그 해제(기존 key는 유지)
   - `오버라이드 삭제`: 오버라이드 레코드 삭제
3) 체인 결과(`version_chains`)를 실제로 바꾸려면
   - `인덱싱…` → `버전 체인 재구축` 체크 → `실행`

## E2E 자동 점검(권장)
- 오버라이드 저장 → 체인 재구축 → `version_chains` 반영까지를 **임시 DB 복제본에서** 자동 검증:
  - `python D:\Workspace_Brain\validate_version_chains_e2e.py`
- 실 DB를 직접 점검/수정하려면(주의):
  - `python D:\Workspace_Brain\validate_version_chains_e2e.py --in-place --project Workspace_Brain`

## 검증(실행 근거)
- 문법 컴파일:
  - `python -m py_compile D:\Workspace_Brain\src\ui\main_window.py D:\Workspace_Brain\src\ui\backend.py`

## 주의/메모
- 오버라이드는 “저장 즉시” 연관문서 리스트에 배지로 표시되지만, **체인 묶음 자체는 재구축 전까지 바뀌지 않습니다.**
  - 재구축은 `scan_all.py --build-version-chains ...` 또는 UI 인덱싱 탭으로 수행합니다.
