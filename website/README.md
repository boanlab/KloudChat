# 사용자 가이드

KloudChat 사용자 가이드와 관리자 가이드의 소스입니다. [Docusaurus](https://docusaurus.io/)로
만들고 GitHub Pages로 게시합니다. 실행 중인 서비스와는 무관한 정적 사이트이며 서비스에서
데이터를 읽지 않습니다.

게시 주소는 <https://boanlab.github.io/KloudChat/> 입니다.

## 구성

| 경로 | 내용 |
|---|---|
| `docs/` | 한국어 본문 31개 |
| `i18n/en/` | 영어 번역. 본문과 화면 문구 |
| `src/pages/index.tsx` | 첫 페이지 |
| `src/components/` | 첫 페이지의 특징 격자와 등장 효과 |
| `static/img/guide/` | 화면 캡처 30장 |

## 로컬에서 보기

```bash
npm ci
npm start
```

한국어만 뜹니다. 영어까지 보려면 `npm start -- --locale en` 을 따로 실행하거나 아래처럼
빌드한 결과를 확인하십시오.

```bash
npm run build
npm run serve
```

## 문서 고치기

`docs/` 의 마크다운을 고치면 됩니다. 한국어를 고쳤으면 `i18n/en/docusaurus-plugin-content-docs/current/`
의 같은 경로에 있는 영어 파일도 함께 고쳐야 두 언어가 어긋나지 않습니다.

첫 페이지나 상단 메뉴 같은 화면 문구를 바꿨다면 아래로 영어 목록을 갱신한 뒤 새로 생긴 항목을
채웁니다.

```bash
npm run write-translations -- --locale en
```

용어는 나누어 번역해도 갈리지 않도록 정해 두었습니다. 새 문서를 쓸 때 그 표를 따르십시오.

## 게시

`main` 의 `website/` 가 바뀌면 `.github/workflows/docs.yml` 이 빌드해서 Pages로 올립니다.
저장소 관리자가 Settings → Pages → Source 를 **GitHub Actions** 로 한 번 바꿔 두어야 첫
배포가 됩니다. 코드 변경 없이 다시 올리려면 Actions 에서 **Docs site** 를 직접 실행하십시오.
