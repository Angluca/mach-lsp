# mach-lsp

A language server for the [Mach](https://github.com/briar-systems/mach) programming
language, built directly on Mach's retained compiler frontend and single-file
editor APIs.

## Status

mach-lsp implements lifecycle, full-text synchronization, diagnostics, hover,
definition, references, rename/prepareRename, document symbols, and completion.

Project documents are analyzed by the compiler's retained frontend API. Each
manifest root owns a stable long-lived compiler Session and one current Project
snapshot; open filesystem documents retain their own text and monotonic revision
and enter the compiler load walk as path overlays and extra module roots. The
selected primary artifact supplies the target, profile, defines, `$project`, and
`$bin` context. Resolve, sema, generic instantiation, and diagnostics therefore
come from the same compiler-owned ModuleEntry rather than LSP copies of compiler
internals.

Roots are independent identity domains, so projects with colliding FQNs can be
queried and rebuilt in either order. Dependency modules already present in an
ancestor graph route to that graph read-only; unrelated nested projects remain
isolated. Files outside a project use the upstream single-file editor API.

Open/change notifications publish fast standalone parse diagnostics when no
current project snapshot exists. A feature request rebuilds a stale snapshot and
the server republishes compiler diagnostics afterward. Watch registration is
considered active only after the client acknowledges it; `didSave`, watched-file
notifications, manifest/lock mtimes, and exact content fingerprints of previously
loaded source paths all drive invalidation and retry. Fingerprint scans are
coalesced to at most once per 250 ms per root, bounding missed-event detection
without hashing a graph on every request. Source overlays are mirrored
under canonical and manifest-raw POSIX spellings (including `src = "./src"`).
Portable Windows/UNC canonicalization is tracked by #157, mach#2998, and
mach-std#472.

Cross-module references and rename walk the retained graph. Rename is restricted
to project-owned declarations, so vendored dependency sources remain read-only.
Completion is currently a flat list of module names, import aliases, and primitive
types rather than a lexical scope view.

The first semantic request still performs a synchronous whole-project frontend
analysis. Syntax-only document symbols do not pay that cost; moving semantic work
off the request path is tracked by #143.

## Building

The compiler and standard library are vendored under `dep/` as git submodules
and declared as git dependencies in `mach.toml`. Pull them, then build with the
Mach toolchain:

```sh
mach dep pull   # vendor dep/mach and dep/mach-std
mach build .    # compile the server
```

The server binary is produced at `out/linux-x86_64/debug/bin/mls`.

## Installing

Copy the built binary onto your `PATH`:

```sh
install -Dm755 out/linux-x86_64/debug/bin/mls ~/.local/bin/mls
```

Then point your editor's LSP client at `mls`; the server speaks the LSP base
protocol over stdin/stdout.

## Tracing

The server speaks JSON-RPC on stdout, so it cannot log there. Set the
`MLS_TRACE` environment variable (to any value) to append a JSON-RPC trace to
`/tmp/mach-lsp.log`; leave it unset — the default — and the server performs no
logging.

## How the compiler dependency is wired

`dep/mach` (id `mach`) provides the `mach.lang.*` compiler and retained frontend
surfaces this server binds to; `dep/mach-std` (id
`std`) provides `std.*`. Both are declared as git dependencies in `mach.toml`
and fetched by `mach dep pull`; Mach tracks `branch/dev` while mach-std tracks
`branch/main` to match Mach's dependency identity.

## Architecture

| Module | Responsibility |
|---|---|
| `main` | entry point; page allocator + server loop |
| `server` | lifecycle state, message loop, method dispatch |
| `transport` | LSP base-protocol framing over stdin/stdout |
| `json` | minimal JSON field extraction and response assembly |
| `documents` | live URI/path/text/version/revision ownership plus fallback `FileId` |
| `diagnostics` | publish compiler snapshot diagnostics, with single-file fallback |
| `positions` | byte offset ⇄ LSP `(line, character)` (UTF-16 columns ⇄ bytes) and span text — the single conversion point |
| `features` | offset → id → symbol query core over the resolve side tables |
| `project` | stable per-root compiler Sessions and retained Project snapshots, overlays, routing, fingerprints, module views, and invalidation |
| `language` | hover / definition / references / rename / documentSymbol / completion request bodies |
| `trace` | append-only debug trace log (`/tmp/mach-lsp.log`) |

## Deferred

- workspace symbol search;
- scope-aware completion (member access after `.`, lexically scoped locals)
  — the resolver's scope chain is internal to the resolve pass and not
  exposed by the side tables.
