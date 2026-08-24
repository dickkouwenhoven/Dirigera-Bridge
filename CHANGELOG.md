# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

---

## [1.0.0] - 2026-08-24

### Added
- Public library API: `Settings`, `load_settings`, and `build_orchestrator` are now
  exported from `dirigera_bridge`, so this bridge can be imported and embedded
  inside a larger multi-bridge application, alongside another vendor's hub bridge,
  without pulling in any CLI-specific code
- `dirigera_bridge/factory.py` — the dependency-wiring composition root, extracted
  from `main.py` so it can be called directly by library consumers
- Four MQTT transport settings: `MQTT_TLS`, `MQTT_RECONNECT`,
  `MQTT_RECONNECT_DELAY_MIN`, `MQTT_RECONNECT_DELAY_MAX` — closes a gap where the
  HASDK's `MQTTSettings` silently fell back to reading undocumented environment
  variables, including a name collision with this project's own
  `RECONNECT_DELAY_MAX` (Dirigera WebSocket reconnect, a different layer entirely)
- `.github/workflows/release.yml` — tag-triggered release pipeline: full CI gate
  (lint, format check, type check, 100%-coverage test run), GitHub Release
  creation with changelog-extracted notes, and PyPI publish (Test PyPI for
  pre-release tags, production PyPI for final tags)
- README sections: "Two ways to use this project" (application vs. library),
  "Enabling MQTT in Home Assistant", and "Setting up an MQTT broker" — each
  fully described rather than just linked out
- Added package publication in workflow `release.yml`.

### Fixed
- `Dockerfile`: `CMD ["python", "main.py"]` pointed at a path that no longer
  existed after `main.py` moved inside the `dirigera_bridge/` package — every
  container start would crash immediately. Now `CMD ["python", "-m", "dirigera_bridge"]`
- Removed a dead `DIRIGERA_PORT` entry from `.env.example`; the Dirigera hub's
  port is fixed at `8443` and was never actually read from the environment

### Changed
- `main.py` slimmed down to CLI/Docker concerns only (`.env` loading, logging
  setup, signal handling); dependency construction and wiring now live in
  `factory.py`
- README's project structure, configuration reference, dependency table, and
  "adding a new device type" instructions updated to match the current
  `dirigera_bridge/` package layout
- Project license clarified as open source (MIT); README's License section now
  points to the `LICENSE` file instead of an outdated "private" notice

---

## [0.1.0] - 2026-07-07

### Added
- Initial release of DirigeraBridge
