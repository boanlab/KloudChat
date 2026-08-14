# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Until 1.0.0, minor releases may contain breaking changes; those are always
listed under **Changed** with a migration note.

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-08-14

Initial public release.

### Added

- Five first-class output kinds sharing one project context: chat, report,
  slides, image, and audio/video. Reports stream section by section, slides
  export as `.pptx`, and video runs as a job with a progress card.
- Self-hosted authentication: argon2id, short-lived access tokens, rotating
  refresh cookies, signup approval and suspension. Each account gets its own
  LiteLLM virtual key at approval, so proxy-side spend is attributable per
  person.
- Credit accounting with an append-only ledger as the source of truth, monthly
  refills, and per-modality pricing units. A model whose price is unknown is
  dropped from the catalogue rather than shown as free.
- Workspace: projects with instructions and knowledge files, skills, memories,
  agents, and MCP connectors with per-tool permissions.
- Read-only sharing by link, scoped either to workspace members or to anyone
  holding the URL.
- `/llm` passthrough, so coding agents authenticate with a user API key and
  spend against that user's account allowance.
- Administration: per-surface feature toggles, branding, backend gateway and
  LiteLLM configuration, SMTP, governance policy and an audit log.
- Korean and English interface, with Korean as the fallback language.
- Container images for `linux/amd64` and `linux/arm64`, published from tagged
  releases.

[Unreleased]: https://github.com/boanlab/kloudchat/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/boanlab/kloudchat/releases/tag/v0.1.0
