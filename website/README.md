# User guide

Source of the KloudChat user guide and administrator guide. It is built with
[Docusaurus](https://docusaurus.io/) and published to GitHub Pages. It is a
static site, separate from the running service, and reads no data from it.

The guide is published at <https://boanlab.github.io/KloudChat/>.

## Layout

| Path | Contents |
|---|---|
| `docs/` | Korean pages |
| `i18n/en/` | English translation: pages and site strings |
| `src/pages/index.tsx` | Landing page |
| `src/components/` | The landing page's screen carousel, feature grid and reveal effect |
| `static/img/guide/` | Screenshots used in the pages |
| `GLOSSARY-en.md` | Term list for the English translation |

## Viewing locally

```bash
npm ci
npm start
```

This serves Korean only. To see English, run `npm start -- --locale en`
separately, or build and serve both:

```bash
npm run build
npm run serve
```

## Editing pages

Edit the Markdown under `docs/`. When you change a Korean page, change the
English file at the same path under `i18n/en/docusaurus-plugin-content-docs/current/`
as well, so the two languages stay in step.

After changing site strings such as the landing page or the top menu, refresh
the English list and fill in the new entries:

```bash
npm run write-translations -- --locale en
```

English terms are fixed in `GLOSSARY-en.md`. Follow that table when
translating a page, and match screen labels to the English strings in
`apps/web/src/lib/i18n.ts`.

## Publishing

When `website/` changes on `main`, `.github/workflows/docs.yml` builds it and
deploys it to Pages. The repository's Settings → Pages → Source must be set to
**GitHub Actions**. To redeploy without a code change, run **Docs site** from
Actions by hand.
