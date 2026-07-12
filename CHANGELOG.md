# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- manifest: Re-touched to RFC-exact totality per the V2 manifest spec (mach#1964/mach#1979).

### Fixed
- deps: Bumped the vendored mach pin (`5b3eef8d` → `8045f941`, v3.5.1) past the required `simd` profile key (mach#1965/mach#2013) and the #1971 flag-day strict-root manifest parse, so the server loads current `mach.toml` manifests instead of rejecting them (`unknown key 'simd'`). (#131)

## [0.9.0] - 2026-07-07

### Changed
- manifest: Migrated manifest layout to comply with the V2 manifest spec.
- dependencies: Changed path-based dependencies to git dependencies pointing to GitHub repositories.
