# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- deps: **advanced the vendored mach pin `v4.7.1` → `v4.18.0`** and mach-std
  `0.22.0` → `0.26.0`. The server analyzes buffers with the vendored compiler
  frontend, so the pin *is* the language version the editor understands: frozen
  at 4.7.1 it reported everything added since - `#[packed]`, a declaration-scope
  `$if` measuring a layout, `$size_of` / `$align_of` / `$length_of` folding in a
  comptime gate, the `#[handle]` / `#[op]` target-owned type and operation
  declarations, riscv32 and the `ilp32` ABI family - as an error against source
  the installed compiler accepts.
- deps: repairs the one frontend API drift the advance surfaces. `comptime.init`
  takes the target's `vector_bits` between `pointer_width` and the compiler
  name, and the target's operation / type-constructor table now reaches the
  front end as data on the comptime context (mach#2888), so a buffer resolving
  under a project seeds `set_target_defs` from its own target the way the
  compiler's own driver does. Without it a `#[handle]` or `#[op]` declaration
  resolves against no definitions at all.
- manifest: `linux-riscv64` moves from `abi = "lp64"` to `abi = "lp64d"`.
  mach#2777 made `lp64` mean what it says - soft float, every float in an
  integer register - where it had always emitted hard-float code. The old
  spelling still builds and would have silently changed the emitted calls.

### Fixed
- json / transport: the decimal formatters wrote `('0' + (v % 10))::u8`, mixing
  a `u8` char literal into `usize` / `i64` arithmetic. Sema types that
  expression at the wider operand and lowering typed it at the literal's own
  width, so mach 4.18 refuses it in its IR verifier (`a conversion's result
  width contradicts its opcode`) - the compiler's own defect, but the source was
  relying on the two passes disagreeing. The width the arithmetic happens at is
  now stated: `'0'::usize` / `'0'::i64`.
- hover: a seeded vector type name (`res.SYM_VECTOR`) rendered as `symbol`
  rather than `type`. Pre-existing, unrelated to the pin advance - `kind_label`
  enumerated symbol kinds 0..11 and fell through on 12.

### Known issues
- project: a manifest declaring `[project] mach = "..."` (mach#2714, new in
  4.15) **fails to load in the editor**, disabling every cross-module feature
  with `this project requires mach <X> or newer; running 0.10.0`. The vendored
  frontend reads its own toolchain version from `MACH_VERSION`, which is
  `$project.version` - the *embedding* project's version - so compiled into
  mls it reports mls's version rather than mach's. Since mach is 4.x and mls is
  0.x, essentially any project adopting the key is refused. The fix belongs
  upstream: mach must carry its own version independently of whoever compiles
  the frontend. mls therefore does not declare the key on itself.

## [0.10.0] - 2026-08-07

### Added
- diagnostics: a diagnostic's `note` and `help` lines now ride the published
  message, and its secondary `related` locations become LSP
  `relatedInformation`. The compiler has always attached all three - `mach
  build` renders them - but the editor dropped them, throwing away the half of
  a mach diagnostic that says what to do about it. A secondary location
  resolves its own URI, so a "previous definition here" pointing into a
  dependency module is a link the client can follow. (#135)
- json: `Buf`, an append-only growable JSON sink. The fixed-shape
  sum-the-lengths-then-append pattern cannot express a payload whose shape is
  data-dependent (a diagnostic's relatedInformation array); every response of
  that kind is built through `Buf` instead.

### Changed
- deps: **advanced the vendored mach pin `v3.6.1` → `v4.7.1`** and mach-std
  `0.20.x` → `0.22.0`. The server analyzes buffers with the vendored compiler
  frontend, so the pin *is* the language version the editor understands: frozen
  at 3.6.1 it reported everything added since - `#[embed]`, the comptime type
  predicates and `$type_name`, `#[naked]` / `#[noinline]`, the unified
  inline-asm grammar, the `platform` target tag - as an error against source
  the installed compiler accepts. Repairs the frontend API drift the advance
  surfaces: the `std.filesystem` rename to `read_string` / `metadata` /
  `write_bytes`, `pointer_width` moving from `RegMachine` to the ISA vtable,
  `build_project_union` returning `outcome.Fail`, and `intern_instance` taking
  the template's nominal TypeId. (#135)
- project: manifest / lockfile staleness is checked in unix nanoseconds rather
  than seconds. The check is an equality test, and a save followed immediately
  by a request lands inside the same second.
- deps: Advanced the vendored mach pin (`8045f941` → `da9b0896`, v3.5.1 → v3.6.1) to the then-current release tip; the only notable delta is the retired x86_64-darwin platform (mach#2104). (#133)
- manifest: Re-touched to RFC-exact totality per the V2 manifest spec (mach#1964/mach#1979).

### Fixed
- deps: Bumped the vendored mach pin (`5b3eef8d` → `8045f941`, v3.5.1) past the required `simd` profile key (mach#1965/mach#2013) and the #1971 flag-day strict-root manifest parse, so the server loads current `mach.toml` manifests instead of rejecting them (`unknown key 'simd'`). (#131)

## [0.9.0] - 2026-07-07

### Changed
- manifest: Migrated manifest layout to comply with the V2 manifest spec.
- dependencies: Changed path-based dependencies to git dependencies pointing to GitHub repositories.
