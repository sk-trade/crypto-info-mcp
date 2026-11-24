# Changelog

All notable changes to this project are documented in this file.

## [0.2.2] - 2026-11-24

### Added

- Added explicit contract and formatting modules, a short repository guide, contribution guidance, and architecture-boundary tests.
- Added locked Ruff linting, formatting, complexity, naming, and Pyright checks to local development and CI.

### Changed

- Refactored market, CoinGecko, Telegram, and Gemini-client flows into smaller named helpers with consistent logging and cleanup.
- Reused one HTTP client for the server lifetime instead of opening a client per upstream request.
- Consolidated duplicate CI workflows and made quality checks gate image builds and production deployment.
- Rebuilt the container as a minimal, pinned, non-root runtime and hardened the Compose service with a read-only filesystem and dropped capabilities.
- Replaced generic image and container names with `crypto-info-mcp` identifiers.

### Fixed

- Corrected unavailable market-cap rank rendering from `N/A위` to `N/A`.
- Kept Telegram disabled when any required setting is missing or empty.
- Preserved CoinGecko query parameters as structured request options and tightened regression coverage for them.

## [0.2.1] - 2026-08-15

### Changed

- Simplified Telegram runtime state and market-result handling without changing MCP contracts.
- Updated the conversational example to the current Google Gen AI SDK while preserving MCP JSON Schema constraints.

### Fixed

- Fixed conversational tool calls, EOF shutdown, and missing `google-genai` dependency guidance.

## [0.2.0] - 2026-07-19

### Added

- Added stable Telegram news references and full-message retrieval through the MCP tool surface.
- Added a credential-free HTTP smoke client that verifies all four required tools and calls the market overview.

### Changed

- Made market, CoinGecko, and Telegram responses bounded and explicit about unavailable, empty, partial, and failed upstream states.
- Published stricter MCP input and output schemas with stable typed error codes for client recovery.
- Bounded and parallelized Gemini tool calls while preserving complete MCP content and structured results.
- Made CI test before Docker builds, restricted production deployment to main pushes or manual runs, and made remote replacement fail fast.
- Aligned local, Docker, Compose, credential, and Telegram session documentation with the verified runtime paths.

### Fixed

- Rejected malformed successful upstream payloads instead of presenting them as valid market or coin data.
- Enforced UTC news windows, usable message identifiers, Telegram timeouts, and guaranteed client cleanup.
- Preserved clear no-news, partial-channel, missing-message, non-text, and upstream-failure behavior.

### Removed

- Removed the obsolete live-network `example/all_test.py` script and its pytest collection exception.
