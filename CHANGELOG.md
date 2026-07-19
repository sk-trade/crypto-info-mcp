# Changelog

All notable changes to this project are documented in this file.

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
