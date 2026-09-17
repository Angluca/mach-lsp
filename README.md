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

The reading thread owns stdin and nothing else. Every message, parsing
included, is handled on one worker thread that owns the sessions, snapshots,
document registry, and feature handlers, so compiler state has exactly one owner
and needs no locking. A cold analysis therefore cannot stop the server from
reading the cancellation, edit, or shutdown behind it, and cannot block the
client mid-write when a `didChange` fills the pipe. A change whose analysis is
superseded by input already queued is coalesced: the text and revision are
recorded, the analysis is deferred, and the debt is paid when the queue drains,
so a burst of keystrokes costs one analysis of the newest text rather than one
per revision.

Roots are independent identity domains, so projects with colliding FQNs can be
queried and rebuilt in either order. Dependency modules already present in an
ancestor graph route to that graph read-only; unrelated nested projects remain
isolated. Files outside a project use the upstream single-file editor API.

Diagnostics own the analysis rather than reporting whichever snapshot a previous
request happened to leave behind: open and change drive the load, so what the
editor shows matches `mach build` from the first notification and does not move
because hover or definition was requested. Documents outside any project fall
back to standalone parse diagnostics. Republishing is scoped to the root that
changed, so an edit in one project does not emit a notification for every open
buffer in another. Watch registration is
considered active only after the client acknowledges it; `didSave`, watched-file
notifications, manifest/lock mtimes, and exact content fingerprints of previously
loaded source paths all drive invalidation and retry. Fingerprint scans are
coalesced to at most once per 250 ms per root, bounding missed-event detection
without hashing a graph on every request. Source overlays are mirrored
under canonical and manifest-raw POSIX spellings (including `src = "./src"`).
Portable Windows/UNC canonicalization is tracked by #157, mach#2998, and
mach-std#472.

While a root rebuilds, the edited buffer is still answered from the snapshot
it has, read through the edits since that snapshot (#251). `textdiff` compares
the snapshot's text with the buffer line by line and narrows each difference to
its bytes. Every position going out is carried across those windows, and a
position touching one answers nothing rather than a place the client no longer
has. Hover, definition, typeDefinition, highlight, signature help, inlay hints,
semantic tokens and the call hierarchy answer this way, and clients that
support it are asked to refresh tokens and hints once the rebuild lands.
References, rename, prepareRename and code actions must not answer from earlier
text, so they are held until a snapshot covers every buffer of their root, and
answered then. A cancel answers a held request at once, and closing its document
answers it with ContentModified. Diagnostics show the buffer's own syntax errors
when it has any, and otherwise the snapshot's semantic diagnostics that avoid
the edits.

Cross-module references and rename walk the retained graph. Rename is restricted
to project-owned declarations, so vendored dependency sources remain read-only.
Completion is currently a flat list of module names, import aliases, and primitive
types rather than a lexical scope view.

The first semantic request still performs a synchronous whole-project frontend
analysis. Syntax-only document symbols do not pay that cost; moving semantic work
off the request path is tracked by #143.

## Known limits

These are the costs of the current design, not defects awaiting a fix. The
figures are from this repository, which analyzes the whole compiler and
standard library (`dep/mach`, `dep/std`): a release build on mach 5.2.1, on an
8-core Ryzen 7 5800X3D. Smaller projects pay proportionally less.

| cost | figure | why |
| --- | --- | --- |
| first semantic answer after opening a project | ~28-30 s | the first load analyzes the whole project before any semantic request can be answered (#143) |
| first rebuild after that load | ~30 s | a root keeps two sessions, and the second is cold until its first build (#252) |
| every later rebuild | ~2 s | the compiler rebuilds the project, not only what an edit touched (#250) |
| analysis worker memory | ~540 MiB after the first load, 0.9-1.0 GiB with both sessions built, ~1.15 GiB peak while rebuilding | the two sessions are the price of rebuilds that never block requests (#248) |

While a rebuild runs, the edited buffer keeps answering from the snapshot it
has (see above), so neither rebuild figure is time without answers. Syntax-only
features never wait on analysis. What remains blocked is the first load.
Semantic requests made during it wait for it to finish. The server keeps reading
input throughout, so it never blocks the editor mid-write, and edits made
meanwhile are coalesced into one analysis.

## Building

The compiler and standard library are vendored under `dep/` as git submodules
and declared as git dependencies in `mach.toml`. Pull them, then build with the
Mach toolchain:

```sh
mach dep pull . # vendor dep/mach and dep/std
mach build .    # compile the server
```

The server binary is produced at `out/linux-x86_64/debug/bin/mls`.

## Installing

Each release carries a prebuilt server for every supported platform. Download
the archive for your platform, check it against `SHA256SUMS`, and put `mls` on
your `PATH`:

```sh
v=0.19.0 t=x86_64-linux
curl -LO https://github.com/briar-systems/mach-lsp/releases/download/v$v/mls-$v-$t.tar.gz
curl -LO https://github.com/briar-systems/mach-lsp/releases/download/v$v/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
tar -xzf mls-$v-$t.tar.gz mls && install -Dm755 mls ~/.local/bin/mls
mls --version
```

Or build it yourself and copy that binary instead:

```sh
install -Dm755 out/linux-x86_64/debug/bin/mls ~/.local/bin/mls
```

### Release assets

The names are a contract: editor extensions download by them.

| asset | contents |
| --- | --- |
| `mls-<version>-<platform>.tar.gz` | `mls` and `LICENSE`, for `x86_64-linux`, `aarch64-linux`, `aarch64-darwin`, `x86_64-darwin` |
| `mls-<version>-x86_64-windows.zip` | `mls.exe` and `LICENSE` |
| `SHA256SUMS` | the SHA-256 of every archive, in `sha256sum` format |

`<version>` has no leading `v`. `mls --version` prints `mls <version>`, and
`initialize` reports the same value as `serverInfo.version`. Every shipped
platform runs the full protocol suite natively in CI. `riscv64-linux` is a build
target without a native runner and is not shipped.

Then point your editor's LSP client at `mls`; the server speaks the LSP base
protocol over stdin/stdout.

## Command line

The public interface is two invocations:

| invocation | behaviour |
| --- | --- |
| `mls` | the language server, speaking LSP over stdin/stdout |
| `mls --version` | prints `mls <version>` and exits |

`mls --worker` is **private**. The server re-launches itself with it to run the
analysis in a supervised child process, so a compiler fault is a child exit the
editor never sees. It is not a stable interface: its name, its arguments and
its behaviour may change in any release. Editors and scripts must not pass it.

## Tracing

The server speaks JSON-RPC on stdout, so it cannot log there. Set the
`MLS_TRACE` environment variable (to any value) to append a trace to
`/tmp/mach-lsp.log`; leave it unset — the default — and the server performs no
logging.

What a trace contains is a separate decision from whether it is on. A message
body is your source code: every `didOpen` carries a whole file and every
`didChange` carries what you just typed. Tracing is normally turned on to see
which requests arrived in what order, which does not need any of that, so by
default the log records only what each message *is* — direction, method, id,
size, timing — and no bodies.

| variable | effect |
|---|---|
| `MLS_TRACE` | enables tracing (any value) |
| `MLS_TRACE=bodies` | also records message bodies, truncated at 512 bytes each |
| `MLS_TRACE_FILE` | appends to this path instead of `/tmp/mach-lsp.log` |

Use `MLS_TRACE=bodies` only when you need the contents of a message, and be
aware that the log will then contain fragments of whatever you have open.

## How the compiler dependency is wired

`dep/mach` (id `mach`) provides the `mach.lang.*` compiler and retained frontend
surfaces this server binds to; `dep/std` (id `std`) provides `std.*`. Both are
declared as git dependencies in `mach.toml`, pinned to release tags (`v5.2.1`
and `v3.2.0`), and fetched by `mach dep pull .`. The committed gitlinks under
`dep/` are the pins; there is no lockfile.

## Architecture

| Module | Responsibility |
|---|---|
| `main` | entry point; page allocator + server loop |
| `server` | lifecycle state, reading loop, and the analysis-thread dispatch |
| `jobs` | bounded message queue feeding the single analysis thread |
| `transport` | LSP base-protocol framing over stdin/stdout |
| `json` | JSON-RPC reading over `std.data.json`, plus LSP payload assembly |
| `documents` | live URI/path/text/version/revision ownership plus fallback `FileId` |
| `diagnostics` | publish compiler snapshot diagnostics, with single-file fallback |
| `positions` | byte offset ⇄ LSP `(line, character)` (UTF-16 columns ⇄ bytes) and span text — the single conversion point, including across a stale snapshot's edits |
| `textdiff` | the windows where a snapshot's text and the client's buffer differ |
| `parked` | requests held until their root's snapshot catches up |
| `features` | offset → id → symbol query core over the resolve side tables |
| `project` | stable per-root compiler Sessions and retained Project snapshots, overlays, routing, fingerprints, module views, and invalidation |
| `language` | hover / definition / references / rename / documentSymbol / completion request bodies |
| `trace` | append-only debug trace log (`/tmp/mach-lsp.log`) |

## Deferred

- workspace symbol search;
- scope-aware completion (member access after `.`, lexically scoped locals)
  — the resolver's scope chain is internal to the resolve pass and not
  exposed by the side tables.
