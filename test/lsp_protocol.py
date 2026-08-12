#!/usr/bin/env python3
"""Minimal live-stdio protocol smoke test for mach-lsp."""

from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

HEADER_MAX = 8 * 1024
BODY_MAX = 16 * 1024 * 1024


class ProtocolError(RuntimeError):
    """Raised when the live protocol session violates an asserted contract."""


class LspSession:
    """Drive one language-server process using LSP stdio framing."""

    def __init__(self, server: Path, cwd: Path, timeout: float) -> None:
        env = os.environ.copy()
        env.pop("MLS_TRACE", None)
        self.timeout = timeout
        self.started = time.monotonic()
        self.proc = subprocess.Popen(
            [str(server)],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        assert self.proc.stderr is not None
        self.inbox: queue.Queue[object] = queue.Queue()
        self.pending: list[dict[str, Any]] = []
        self.stderr_chunks: list[bytes] = []
        self.next_id = 1
        self.message_count = 0
        self.timings: list[tuple[str, float]] = []
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.stderr_reader = threading.Thread(target=self._stderr_loop, daemon=True)
        self.reader.start()
        self.stderr_reader.start()

    def _read_loop(self) -> None:
        try:
            while True:
                headers: dict[bytes, bytes] = {}
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        return
                    if line in (b"\r\n", b"\n"):
                        break
                    name, separator, value = line.partition(b":")
                    if not separator:
                        raise ProtocolError(f"malformed response header: {line!r}")
                    headers[name.strip().lower()] = value.strip()
                raw_length = headers.get(b"content-length")
                if raw_length is None:
                    raise ProtocolError("response has no Content-Length header")
                length = int(raw_length)
                body = self.proc.stdout.read(length)
                if length <= 0 or len(body) != length:
                    raise ProtocolError("response body length does not match Content-Length")
                message = json.loads(body)
                if not isinstance(message, dict):
                    raise ProtocolError(f"JSON-RPC message is not an object: {message!r}")
                self.inbox.put(message)
        except BaseException as error:
            self.inbox.put(error)
        finally:
            self.inbox.put(None)

    def _stderr_loop(self) -> None:
        while True:
            chunk = self.proc.stderr.read(4096)
            if not chunk:
                return
            self.stderr_chunks.append(chunk)

    def _send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode()
        frame = f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload
        try:
            self.proc.stdin.write(frame)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise ProtocolError(f"server stdin closed; stderr: {self.stderr_text()}") from error

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification."""
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a request, await its id, and retain its latency."""
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        started = time.monotonic()
        self._send(message)
        response = self.wait_for(lambda item: item.get("id") == request_id, f"response to {method}")
        self.timings.append((f"{method}#{request_id}", time.monotonic() - started))
        if "error" in response:
            raise ProtocolError(f"{method} returned {response['error']!r}")
        return response

    def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        description: str,
    ) -> dict[str, Any]:
        """Wait for one message while retaining unrelated notifications."""
        for index, message in enumerate(self.pending):
            if predicate(message):
                return self.pending.pop(index)
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProtocolError(f"timed out waiting for {description}; stderr: {self.stderr_text()}")
            try:
                item = self.inbox.get(timeout=remaining)
            except queue.Empty as error:
                raise ProtocolError(f"timed out waiting for {description}") from error
            if item is None:
                raise ProtocolError(f"server exited while waiting for {description}; stderr: {self.stderr_text()}")
            if isinstance(item, BaseException):
                raise ProtocolError(f"response reader failed: {item}") from item
            assert isinstance(item, dict)
            self.message_count += 1
            if predicate(item):
                return item
            self.pending.append(item)

    def diagnostics(self, uri: str) -> dict[str, Any]:
        """Wait for the next diagnostics notification for a document."""
        return self.wait_for(
            lambda item: item.get("method") == "textDocument/publishDiagnostics"
            and isinstance(item.get("params"), dict)
            and item["params"].get("uri") == uri,
            f"diagnostics for {uri}",
        )

    def finish(self, send_exit: bool = True) -> tuple[int, float, int]:
        """Perform shutdown/exit and return process telemetry."""
        response = self.request("shutdown")
        require(response.get("result", object()) is None, f"invalid shutdown response: {response!r}")
        if send_exit:
            self.notify("exit")
        self.proc.stdin.close()
        try:
            code = self.proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired as error:
            self.proc.kill()
            self.proc.wait()
            raise ProtocolError("server did not exit after shutdown") from error
        self._join()
        require(code == 0, f"server exited with {code}; stderr: {self.stderr_text()}")
        return code, time.monotonic() - self.started, self.message_count

    def abort(self) -> None:
        """Stop a failed session without hiding its assertion."""
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._join()

    def _join(self) -> None:
        self.reader.join(timeout=1)
        self.stderr_reader.join(timeout=1)

    def stderr_text(self) -> str:
        """Return captured server stderr."""
        return b"".join(self.stderr_chunks).decode(errors="replace").strip()


def require(condition: bool, message: str) -> None:
    """Raise a readable protocol assertion failure."""
    if not condition:
        raise ProtocolError(message)


def assert_position(value: Any, label: str) -> None:
    """Check the JSON shape of an LSP Position."""
    require(isinstance(value, dict), f"{label} is not an object")
    for field in ("line", "character"):
        require(type(value.get(field)) is int and value[field] >= 0, f"{label}.{field} is invalid")


def assert_range(value: Any, label: str) -> None:
    """Check the JSON shape of an LSP Range."""
    require(isinstance(value, dict), f"{label} is not an object")
    assert_position(value.get("start"), f"{label}.start")
    assert_position(value.get("end"), f"{label}.end")


def assert_diagnostics(message: dict[str, Any], nonempty: bool) -> None:
    """Check publishDiagnostics and the shape of every entry."""
    params = message.get("params")
    require(isinstance(params, dict), "diagnostics params are missing")
    diagnostics = params.get("diagnostics")
    require(isinstance(diagnostics, list), "diagnostics is not an array")
    require(bool(diagnostics) == nonempty, f"unexpected diagnostics: {diagnostics!r}")
    for index, diagnostic in enumerate(diagnostics):
        label = f"diagnostics[{index}]"
        require(isinstance(diagnostic, dict), f"{label} is not an object")
        assert_range(diagnostic.get("range"), f"{label}.range")
        severity = diagnostic.get("severity")
        require(type(severity) is int and 1 <= severity <= 4, f"{label}.severity is invalid")
        require(diagnostic.get("source") == "mach", f"{label}.source is invalid")
        require(bool(diagnostic.get("message")), f"{label}.message is empty")


def write_project(parent: Path, project_id: str, value: int) -> tuple[Path, Path, str]:
    """Create a temporary dependency-free Mach project."""
    root = parent / project_id
    source = root / "src"
    source.mkdir(parents=True)
    (root / "mach.toml").write_text(
        f"""[project]
id = "{project_id}"
version = "0.1.0"
src = "src"
out = "out/{{target.name}}/{{profile.name}}"

[target.linux-x86_64]
isa = "x86_64"
os = "linux"
abi = "sysv64"

[profile.debug]
opt = 0
debug = true
simd = "scalarize"

[artifact.app]
kind = "bin"
entry = "main.mach"
out = "bin/app"
targets = ["*"]
link = []
need = []
""",
        encoding="utf-8",
    )
    text = f"""use {project_id}.defs.answer;

pub fun main() i32 {{
    ret answer;
}}
"""
    main = source / "main.mach"
    definition = source / "defs.mach"
    main.write_text(text, encoding="utf-8")
    definition.write_text(f"pub val answer: i32 = {value};\n", encoding="utf-8")
    return main, definition, text


def assert_definition(session: LspSession, main: Path, definition: Path, text: str) -> None:
    """Check that `answer` resolves into the expected project root."""
    lines = text.splitlines()
    line = next(index for index, value in enumerate(lines) if "ret answer" in value)
    response = session.request(
        "textDocument/definition",
        {
            "textDocument": {"uri": main.as_uri()},
            "position": {"line": line, "character": lines[line].index("answer") + 1},
        },
    )
    result = response.get("result")
    require(isinstance(result, dict), f"definition is not a Location: {result!r}")
    require(result.get("uri") == definition.as_uri(), f"definition escaped its root: {result!r}")
    assert_range(result.get("range"), "definition.range")


def run_smoke(server: Path, timeout: float) -> tuple[tuple[int, float, int], list[tuple[str, float]]]:
    """Run lifecycle, diagnostics, synchronization, and multi-root coverage."""
    with tempfile.TemporaryDirectory(prefix="mls-protocol-") as directory:
        root = Path(directory).resolve()
        alpha = write_project(root, "alpha", 11)
        beta = write_project(root, "beta", 22)
        scratch_uri = (root / "scratch.mach").as_uri()
        session = LspSession(server, root, timeout)
        finished = False
        try:
            response = session.request(
                "initialize",
                {"processId": os.getpid(), "rootUri": root.as_uri(), "capabilities": {}},
            )
            result = response.get("result")
            require(isinstance(result, dict), f"invalid initialize result: {result!r}")
            capabilities = result.get("capabilities")
            require(isinstance(capabilities, dict), "initialize capabilities are missing")
            require(capabilities.get("textDocumentSync") == 1, "full-text sync is not advertised")
            session.notify("initialized", {})

            session.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": scratch_uri,
                        "languageId": "mach",
                        "version": 1,
                        "text": "pub fun broken(",
                    }
                },
            )
            assert_diagnostics(session.diagnostics(scratch_uri), True)
            session.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": scratch_uri, "version": 2},
                    "contentChanges": [{"text": "pub fun fixed() i32 { ret 0; }\n"}],
                },
            )
            assert_diagnostics(session.diagnostics(scratch_uri), False)
            session.notify("textDocument/didClose", {"textDocument": {"uri": scratch_uri}})
            assert_diagnostics(session.diagnostics(scratch_uri), False)

            for main, _, text in (alpha, beta):
                session.notify(
                    "textDocument/didOpen",
                    {
                        "textDocument": {
                            "uri": main.as_uri(),
                            "languageId": "mach",
                            "version": 1,
                            "text": text,
                        }
                    },
                )
                assert_diagnostics(session.diagnostics(main.as_uri()), False)

            assert_definition(session, *alpha)
            assert_definition(session, *beta)
            assert_definition(session, *alpha)
            for main, _, _ in (alpha, beta):
                session.notify("textDocument/didClose", {"textDocument": {"uri": main.as_uri()}})

            telemetry = session.finish()
            finished = True
            return telemetry, session.timings
        finally:
            if not finished:
                session.abort()


def run_bad_frame(server: Path, frame: bytes, timeout: float, label: str) -> None:
    """Require one malformed or oversized frame to terminate with status 1."""
    with tempfile.TemporaryDirectory(prefix="mls-protocol-bad-") as directory:
        try:
            result = subprocess.run(
                [str(server)],
                cwd=directory,
                input=frame,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ProtocolError(f"{label}: server did not terminate") from error
    require(result.returncode == 1, f"{label}: expected exit 1, got {result.returncode}")
    require(result.stdout == b"", f"{label}: server emitted a partial response")


def run_clean_eof(server: Path, timeout: float) -> None:
    """A clean EOF after shutdown is not a malformed-frame failure."""
    with tempfile.TemporaryDirectory(prefix="mls-protocol-eof-") as directory:
        root = Path(directory).resolve()
        session = LspSession(server, root, timeout)
        finished = False
        try:
            session.request("initialize", {"rootUri": root.as_uri(), "capabilities": {}})
            exit_code, _, _ = session.finish(send_exit=False)
            require(exit_code == 0, f"clean EOF after shutdown exited {exit_code}")
            finished = True
        finally:
            if not finished:
                session.abort()


def run_transport_regressions(server: Path, timeout: float) -> None:
    """Exercise malformed/truncated and resource-bounded input framing."""
    cases = (
        (b"X-Header: value\r\n\r\n", "missing Content-Length"),
        (b"Content-Length: 4\r\n", "truncated header"),
        (b"Content-Length: 12junk\r\n\r\n", "malformed Content-Length"),
        (b"Content-Length: 4\r\nContent-Length: 4\r\n\r\nnull", "duplicate Content-Length"),
        (b"Content-Length: 20\r\n\r\n{}", "truncated body"),
        (f"Content-Length: {BODY_MAX + 1}\r\n\r\n".encode(), "oversized body"),
        (b"Content-Length: 999999999999999999999999999999999999\r\n\r\n", "overflowing length"),
        (b"X-Fill: " + (b"x" * HEADER_MAX), "oversized header"),
    )
    for frame, label in cases:
        run_bad_frame(server, frame, timeout, label)


def probe_closed_stdout(server: Path, timeout: float, restore_signals: bool) -> int:
    """Close the client read end, trigger output, and return the process status."""
    with tempfile.TemporaryDirectory(prefix="mls-protocol-pipe-") as directory:
        read_fd, write_fd = os.pipe()
        proc = subprocess.Popen(
            [str(server)],
            cwd=directory,
            stdin=subprocess.PIPE,
            stdout=write_fd,
            stderr=subprocess.PIPE,
            close_fds=True,
            restore_signals=restore_signals,
        )
        os.close(write_fd)
        os.close(read_fd)
        assert proc.stdin is not None
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode()
        try:
            proc.stdin.write(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
            proc.stdin.flush()
            proc.stdin.close()
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            proc.kill()
            proc.wait()
            raise ProtocolError("closed stdout reader: server did not terminate") from error


def main() -> int:
    """Run the suite and print request timing plus process-exit telemetry."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", type=Path, help="path to the debug mls executable")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds allowed per response")
    args = parser.parse_args()
    server = args.server.resolve()
    if not server.is_file() or not os.access(server, os.X_OK):
        parser.error(f"server is not executable: {server}")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        (exit_code, elapsed, message_count), timings = run_smoke(server, args.timeout)
        run_clean_eof(server, args.timeout)
        run_transport_regressions(server, args.timeout)
        closed_stdout_status = probe_closed_stdout(server, args.timeout, True)
        suppressed_status = probe_closed_stdout(server, args.timeout, False)
        require(suppressed_status == 1, f"suppressed SIGPIPE: expected exit 1, got {suppressed_status}")
        if os.name != "posix" or closed_stdout_status != -signal.SIGPIPE:
            require(closed_stdout_status == 1, f"closed stdout reader: expected exit 1, got {closed_stdout_status}")
    except Exception as error:
        print(f"protocol smoke: FAIL: {error}", file=sys.stderr)
        return 1
    print(f"protocol smoke: PASS ({message_count} messages, exit {exit_code}, {elapsed:.3f}s)")
    print("  clean EOF after shutdown: exit 0")
    print("  malformed/oversized frames: 8 rejected with exit 1")
    print("  closed stdout reader with inherited SIG_IGN: exit 1")
    if os.name == "posix" and closed_stdout_status == -signal.SIGPIPE:
        print("  closed stdout reader: SIGPIPE (blocked by mach-std#468)")
    else:
        print("  closed stdout reader: exit 1")
    for label, duration in timings:
        print(f"  {label}: {duration:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
