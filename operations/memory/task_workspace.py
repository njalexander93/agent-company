#!/usr/bin/env python3
"""Local, cooperative issue workspaces. No network calls or runtime authority.

All store access uses no-follow directory descriptors. A persistent flock fences
supported writers; a durable roll-forward intent couples payload, events and state.
See docs/task-workspace.md for the public request contract and host limitations.
"""
from __future__ import annotations

import argparse
import base64
import copy
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

MAX_REQUEST = 96 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_EVENTS = 16 * 1024 * 1024
MAX_ARCHIVE = 32 * 1024 * 1024
DOCUMENT_LIMIT = 256 * 1024
ARCHIVE_CHUNK = 64 * 1024
ISSUE = re.compile(r"[A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,9}", re.ASCII)
NOTE = re.compile(r"context/[a-z0-9][a-z0-9._-]{0,79}\.md", re.ASCII)
SEGMENT = re.compile(r"events-([0-9]{12})-([0-9]{12})\.jsonl", re.ASCII)
DIGEST = re.compile(r"[0-9a-f]{64}")
TERMINAL = {"completed", "cancelled", "failed"}
FAILPOINT = None  # Test-only callable; never accepted in requests or environment.


class WorkspaceError(Exception):
    """Represent a bounded workspace failure without embedding task content.
    """
    def __init__(self, code, action="Inspect the assigned workspace; preserve its bytes."):
        """Store the public diagnostic code and safe recovery instruction.

        Args:
            code: Public bounded diagnostic code.
            action: Safe recovery instruction to attach to the diagnostic.
        """
        # Initialize the bounded public diagnostic and recovery instruction.
        self.code, self.action = code, action
        super().__init__(code)


def require(condition, code="INVALID_REQUEST", action=None):
    """Stop an operation when its required boundary condition is false.

    Args:
        condition: Required predicate for continuing the operation.
        code: Public bounded diagnostic code.
        action: Safe recovery instruction to attach to the diagnostic.

    Raises:
        WorkspaceError: If the condition does not hold.
    """
    # Reject the failed precondition with its public diagnostic.
    if not condition:
        raise WorkspaceError(code, action or "Inspect the request and retry with explicit identities.")


def canonical(value):
    """Serialize a value into the deterministic UTF-8 form used by digests.

    Args:
        value: Value to validate or encode under this helper's contract.

    Returns:
        Canonical JSON bytes.

    Raises:
        ValueError: If a value cannot be represented as finite JSON.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha(data):
    """Identify exact bytes with a hexadecimal SHA-256 digest.

    Args:
        data: Exact bytes whose content identity is required.

    Returns:
        The lowercase 64-character digest.
    """
    return hashlib.sha256(data).hexdigest()


def now():
    """Record the current UTC time for local lifecycle evidence.

    Returns:
        An ISO 8601 timestamp with timezone.
    """
    return datetime.now(timezone.utc).isoformat()


def fault(point):
    """Invoke the process-local test failpoint without accepting external hooks.

    Args:
        point: Named internal transaction boundary for a test injection.

    Raises:
        Exception: Propagated from an installed test failpoint.
    """
    # Run only an explicitly installed process-local test injection.
    if FAILPOINT:
        FAILPOINT(point)


def token(value):
    """Validate a bounded printable ASCII identity token.

    Args:
        value: Value to validate or encode under this helper's contract.

    Returns:
        The validated token.

    Raises:
        WorkspaceError: If the token is empty, oversized or contains unsafe characters.
    """
    # Enforce the explicit identity and input contract.
    require(isinstance(value, str) and 0 < len(value) <= 160 and
            all(32 < ord(c) < 127 for c in value))
    return value


def issue_id(value):
    """Validate the strict issue identifier used as a directory component.

    Args:
        value: Value to validate or encode under this helper's contract.

    Returns:
        The validated issue identifier.

    Raises:
        WorkspaceError: If the identifier violates the issue grammar.
    """
    # Enforce INVALID_ISSUE boundaries.
    require(isinstance(value, str) and ISSUE.fullmatch(value), "INVALID_ISSUE")
    return value


def payload_path(value, events=False):
    """Allow only supported issue payload paths and optional event files.

    Args:
        value: Value to validate or encode under this helper's contract.
        events: Whether the event stream and immutable segment paths are permitted.

    Returns:
        The validated relative path.

    Raises:
        WorkspaceError: If the path is outside the payload grammar.
    """
    # Enforce UNSAFE_PATH boundaries.
    require(isinstance(value, str) and ".." not in value and
            (value == "roadmap.md" or NOTE.fullmatch(value) or
             (events and (value == "events.jsonl" or SEGMENT.fullmatch(value)))), "UNSAFE_PATH")
    return value


def decode(value, limit=MAX_FILE):
    """Decode strict base64 while enforcing the decoded byte budget.

    Args:
        value: Strict base64 text to decode.
        limit: Maximum allowed file or decoded byte count.

    Returns:
        The decoded bytes.

    Raises:
        WorkspaceError: If encoding or size validation fails.
    """
    # Enforce SIZE_LIMIT boundaries.
    require(isinstance(value, str) and len(value) <= (limit + 2) // 3 * 4, "SIZE_LIMIT")
    # Attempt this phase while retaining its error and cleanup paths.
    try:
        result = base64.b64decode(value, validate=True)
    except ValueError:
        # Preserve or translate this failure according to the enclosing contract.
        raise WorkspaceError("INTEGRITY_ERROR") from None
    # Enforce SIZE_LIMIT boundaries.
    require(len(result) <= limit, "SIZE_LIMIT")
    return result


def encode(value):
    """Encode bytes for reconstructable JSON archive and transaction records.

    Args:
        value: Exact bytes to represent without loss.

    Returns:
        ASCII base64 text.
    """
    return base64.b64encode(value).decode("ascii")


def strict_json(data):
    """Parse JSON while rejecting duplicate keys and non-finite constants.

    Args:
        data: JSON bytes or text received from a bounded source.

    Returns:
        The decoded JSON value.

    Raises:
        WorkspaceError: If the input is invalid or ambiguous JSON.
    """
    def pairs(items):
        """Build one JSON object without permitting duplicate field names.

        Args:
            items: Ordered key/value pairs from the JSON parser.

        Returns:
            The unique-key object.

        Raises:
            WorkspaceError: If any key occurs more than once.
        """
        # Prepare the pairs values for the next contract boundary.
        result = {}
        # Process each (key, value) under the same validation boundary.
        for key, value in items:
            # Enforce INVALID_REQUEST boundaries.
            require(key not in result, "INVALID_REQUEST")
            # Prepare the pairs values for the next contract boundary.
            result[key] = value
        return result
    # Attempt this phase while retaining its error and cleanup paths.
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(WorkspaceError("INVALID_REQUEST")))
    except (ValueError, UnicodeError):
        # Preserve or translate this failure according to the enclosing contract.
        raise WorkspaceError("INVALID_REQUEST") from None


class Directory:
    """Hold an owned no-follow directory descriptor for bounded filesystem operations.
    """
    def __init__(self, fd, private=False):
        """Validate and retain an opened directory descriptor and its identity.

        Args:
            fd: Already opened directory file descriptor; owned by this handle.
            private: Whether to reject group/world-writable directory permissions.

        Raises:
            WorkspaceError: If ownership, type or required private permissions are unsafe.
        """
        # Prepare the __init__ values for the next contract boundary.
        self.fd = fd
        # Read __init__ inputs through the scoped file interface.
        s = os.fstat(fd)
        # Enforce UNSAFE_PATH boundaries.
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == os.getuid(), "UNSAFE_PATH")
        require(not private or not s.st_mode & 0o022, "UNSAFE_PATH")
        # Prepare the __init__ values for the next contract boundary.
        self.identity = [s.st_dev, s.st_ino]

    @classmethod
    def absolute(cls, path):
        """Open each absolute-path component without following symlinks.

        Args:
            path: Path to validate or access within the stated filesystem boundary.

        Returns:
            A validated Directory whose descriptor the caller must close.

        Raises:
            WorkspaceError: If the path or directory identity is unsafe.
            OSError: If any component cannot be opened.
        """
        # Prepare the absolute values for the next contract boundary.
        path = Path(path)
        # Enforce UNSAFE_PATH boundaries.
        require(path.is_absolute() and ".." not in path.parts, "UNSAFE_PATH")
        # Read absolute inputs through the scoped file interface.
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            # Process each name under the same validation boundary.
            for name in path.parts[1:]:
                # Read absolute inputs through the scoped file interface.
                nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                # Release the scoped resource or remove the already validated direct entry.
                os.close(fd)
                # Prepare the absolute values for the next contract boundary.
                fd = nxt
            return cls(fd)
        except BaseException:
            # Preserve or translate this failure according to the enclosing contract.
            os.close(fd)
            raise

    def close(self):
        """Release this directory descriptor.

        Raises:
            OSError: If the descriptor cannot be closed.
        """
        # Release the scoped resource or remove the already validated direct entry.
        os.close(self.fd)

    def __enter__(self):
        """Expose the directory to a context-managed operation.

        Returns:
            This directory handle.
        """
        return self

    def __exit__(self, *args):
        """Close the descriptor when the directory context ends.

        Args:
            args: Context-manager exception triple, ignored during descriptor cleanup.

        Raises:
            OSError: If descriptor cleanup fails.
        """
        # Release the scoped resource or remove the already validated direct entry.
        self.close()

    def exists(self, name):
        """Check entry presence without following a final symlink.

        Args:
            name: Direct entry name relative to the opened directory.

        Returns:
            Whether a directory entry exists.

        Raises:
            OSError: If inspection fails for a reason other than absence.
        """
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            # Prepare the exists values for the next contract boundary.
            os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            # Preserve or translate this failure according to the enclosing contract.
            return False

    def child(self, name, create=False, private=True):
        """Open or create one direct child directory without following links.

        Args:
            name: Direct entry name relative to the opened directory.
            create: Whether an absent child directory may be created.
            private: Whether to reject group/world-writable directory permissions.

        Returns:
            A validated child Directory owned by the caller.

        Raises:
            WorkspaceError: If the child name or directory is unsafe.
            OSError: If creation or opening fails.
        """
        # Enforce UNSAFE_PATH boundaries.
        require("/" not in name and name not in {"", ".", ".."}, "UNSAFE_PATH")
        # Handle the case create.
        if create:
            # Attempt this phase while retaining its error and cleanup paths.
            try:
                # Prepare the child values for the next contract boundary.
                os.mkdir(name, 0o700, dir_fd=self.fd)
                # Publish validated content and flush the required filesystem boundary.
                os.fsync(self.fd)
            except FileExistsError:
                # Preserve or translate this failure according to the enclosing contract.
                pass
        return Directory(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=self.fd), private)

    def read(self, name, limit=MAX_REQUEST):
        """Read a bounded owned regular file without following links.

        Args:
            name: Direct entry name relative to the opened directory.
            limit: Maximum allowed file or decoded byte count.

        Returns:
            The complete validated file bytes.

        Raises:
            WorkspaceError: If type, ownership, link count, permissions or size are unsafe.
            OSError: If the entry cannot be opened or read.
        """
        # Read read inputs through the scoped file interface.
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            # Read read inputs through the scoped file interface.
            s = os.fstat(fd)
            # Enforce UNSAFE_PATH, SIZE_LIMIT boundaries.
            require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and
                    s.st_uid == os.getuid() and not s.st_mode & 0o022, "UNSAFE_PATH")
            require(s.st_size <= limit, "SIZE_LIMIT")
            # Keep filesystem resources scoped to this read or publication phase.
            with os.fdopen(fd, "rb", closefd=False) as stream:
                result = stream.read(limit + 1)
            # Enforce SIZE_LIMIT boundaries.
            require(len(result) <= limit, "SIZE_LIMIT")
            return result
        finally:
            # Release temporary resources even after a partial failure.
            os.close(fd)

    def json(self, name):
        """Read a safe file and decode strict JSON from its bytes.

        Args:
            name: Direct entry name relative to the opened directory.

        Returns:
            The decoded JSON value.

        Raises:
            WorkspaceError: If the file or JSON is invalid.
            OSError: If the file cannot be read.
        """
        return strict_json(self.read(name))

    def write(self, name, data):
        """Publish bytes through a flushed private temporary file and atomic replacement.

        Args:
            name: Direct entry name relative to the opened directory.
            data: Exact input bytes to inspect or transform.

        Raises:
            WorkspaceError: If the entry name or existing destination is unsafe.
            OSError: If publication, flushing or cleanup fails.
        """
        # Enforce UNSAFE_PATH boundaries.
        require("/" not in name and name not in {"", ".", ".."}, "UNSAFE_PATH")
        # Validate any existing entry before reusing or replacing it.
        if self.exists(name):
            self.read(name)  # Refuse links/special files before replacement.
        # Prepare the write values for the next contract boundary.
        temp = ".write-" + uuid.uuid4().hex
        # Read write inputs through the scoped file interface.
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            # Keep filesystem resources scoped to this read or publication phase.
            with os.fdopen(fd, "wb", closefd=False) as stream:
                # Publish validated content and flush the required filesystem boundary.
                stream.write(data)
                # Prepare the write values for the next contract boundary.
                stream.flush()
                # Publish validated content and flush the required filesystem boundary.
                os.fsync(fd)
            # Publish validated content and flush the required filesystem boundary.
            os.replace(temp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            # Release temporary resources even after a partial failure.
            os.close(fd)
            # Validate any existing entry before reusing or replacing it.
            if self.exists(temp):
                os.unlink(temp, dir_fd=self.fd)

    def put(self, name, value):
        """Publish a value as canonical JSON through the safe file writer.

        Args:
            name: Direct entry name relative to the opened directory.
            value: Value to validate or encode under this helper's contract.

        Raises:
            WorkspaceError: If destination validation fails.
            OSError: If publication fails.
        """
        # Publish validated content and flush the required filesystem boundary.
        self.write(name, canonical(value))

    def unlink(self, name):
        """Remove a direct entry and flush the containing directory.

        Args:
            name: Direct entry name relative to the opened directory.

        Raises:
            OSError: If removal or directory flushing fails.
        """
        # Release the scoped resource or remove the already validated direct entry.
        os.unlink(name, dir_fd=self.fd)
        # Publish validated content and flush the required filesystem boundary.
        os.fsync(self.fd)

    @contextmanager
    def lock(self, name="lock", timeout=1.0):
        """Hold a bounded exclusive flock on a persistent safe lock file.

        Args:
            name: Direct entry name relative to the opened directory.
            timeout: Maximum seconds to wait for lock acquisition.

        Returns:
            A context manager that yields while the issue lock is held.

        Raises:
            WorkspaceError: If the lock is unsafe or its deadline expires.
            OSError: If lock-file access fails.
        """
        # Set the monotonic deadline for bounded lock acquisition.
        end = time.monotonic() + timeout
        # Retry until success or the bounded deadline is reached.
        while True:
            # Attempt this phase while retaining its error and cleanup paths.
            try:
                # Read lock inputs through the scoped file interface.
                fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.fd)
                # Publish validated content and flush the required filesystem boundary.
                os.fsync(self.fd)
                # Prepare the lock values for the next contract boundary.
                break
            except FileExistsError:
                # Preserve or translate this failure according to the enclosing contract.
                try:
                    # Read lock inputs through the scoped file interface.
                    fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
                    # Prepare the lock values for the next contract boundary.
                    break
                except FileNotFoundError:
                    # Preserve or translate this failure according to the enclosing contract.
                    require(time.monotonic() < end, "BUSY")
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            # Read lock inputs through the scoped file interface.
            s = os.fstat(fd)
            # Enforce UNSAFE_PATH boundaries.
            require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and
                    s.st_uid == os.getuid() and not s.st_mode & 0o022, "UNSAFE_PATH")
            # Set the monotonic deadline for bounded lock acquisition.
            end = time.monotonic() + timeout
            # Retry until success or the bounded deadline is reached.
            while True:
                # Attempt this phase while retaining its error and cleanup paths.
                try:
                    # Prepare the lock values for the next contract boundary.
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    # Preserve or translate this failure according to the enclosing contract.
                    require(time.monotonic() < end, "BUSY", "Retry after the current writer finishes.")
                    # Prepare the lock values for the next contract boundary.
                    time.sleep(0.01)
            # Prepare the lock values for the next contract boundary.
            yield
        finally:
            # Release temporary resources even after a partial failure.
            os.close(fd)


def git(path, *args):
    """Run a bounded Git query without an interactive prompt.

    Args:
        path: Path to validate or access within the stated filesystem boundary.
        args: Git argument words passed without shell interpolation.

    Returns:
        Stripped UTF-8 command output.

    Raises:
        subprocess.SubprocessError: If Git fails or exceeds the timeout.
    """
    # Select and decode the documented command input.
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                            text=True, timeout=2, check=False)
    # Enforce REPOSITORY_MISMATCH boundaries.
    require(result.returncode == 0, "REPOSITORY_MISMATCH")
    return result.stdout.strip()


def repository(path):
    """Resolve a non-bare worktree to its common Git directory and member roots.

    Args:
        path: Path to validate or access within the stated filesystem boundary.

    Returns:
        A tuple of worktree root, common Git directory and worktree paths.

    Raises:
        WorkspaceError: If the repository is unsupported or membership is inconsistent.
        subprocess.SubprocessError: If Git metadata cannot be queried.
    """
    # Prepare the repository values for the next contract boundary.
    root = Path(git(path, "rev-parse", "--show-toplevel"))
    # Enforce HOST_UNSUPPORTED boundaries.
    require(git(root, "rev-parse", "--is-bare-repository") == "false", "HOST_UNSUPPORTED")
    # Prepare the repository values for the next contract boundary.
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    trees = [Path(line[9:]) for line in git(root, "worktree", "list", "--porcelain").splitlines()
             if line.startswith("worktree ")]
    # Enforce REPOSITORY_MISMATCH boundaries.
    require(root in trees, "REPOSITORY_MISMATCH")
    return root, common, trees


def fs_identity(path):
    """Read an absolute directory device/inode identity through safe traversal.

    Args:
        path: Path to validate or access within the stated filesystem boundary.

    Returns:
        The directory device and inode pair.

    Raises:
        WorkspaceError: If directory validation fails.
        OSError: If the path cannot be opened.
    """
    # Keep filesystem resources scoped to this read or publication phase.
    with Directory.absolute(path) as directory:
        return directory.identity


def register(request):
    """Register a main-worktree store and optional explicit startup assignment.

    Args:
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The repository identity and successful registration result.

    Raises:
        WorkspaceError: If repository identities or startup scope conflict.
        OSError: If store creation or persistence fails.
    """
    # Prepare the register values for the next contract boundary.
    root, common, trees = repository(request["worktree"])
    main = Path(request["main_worktree"])
    # Enforce REPOSITORY_MISMATCH boundaries.
    require(main == trees[0] and main in trees, "REPOSITORY_MISMATCH")
    require(repository(main)[1] == common, "REPOSITORY_MISMATCH")
    # Prepare the register values for the next contract boundary.
    identity = {"schema_version": 1, "common": str(common), "main": str(main),
                "common_identity": fs_identity(common), "main_identity": fs_identity(main)}
    # Hold the required locks while validating or publishing shared state.
    with Directory.absolute(main) as directory, directory.child(".task", True) as task, \
            task.child(".control", True) as control, control.lock("registration.lock"):
        # Validate any existing entry before reusing or replacing it.
        if control.exists("repository.json"):
            # Read register inputs through the scoped file interface.
            existing = control.json("repository.json")
            # Enforce REPOSITORY_MISMATCH boundaries.
            require(all(existing[k] == v for k, v in identity.items()), "REPOSITORY_MISMATCH")
            # Prepare the register values for the next contract boundary.
            identity = existing
        else:
            # Prepare the register values for the next contract boundary.
            identity["repo_id"] = str(uuid.uuid4())
            # Publish validated content and flush the required filesystem boundary.
            control.put("repository.json", identity)
        # Keep filesystem resources scoped to this read or publication phase.
        with Directory.absolute(root) as worktree, worktree.child(".task", True) as local:
            # Validate any existing entry before reusing or replacing it.
            if local.exists(".repository.json"):
                require(local.json(".repository.json") == identity, "REPOSITORY_MISMATCH")
            # Publish validated content and flush the required filesystem boundary.
            local.put(".repository.json", identity)
            # Handle the case request.get('startup') is not None.
            if request.get("startup") is not None:
                # Prepare the register values for the next contract boundary.
                setup = request["startup"]
                # Enforce the explicit identity and input contract.
                require(isinstance(setup, dict) and set(setup) == {"issue_id", "issue_uuid", "packet", "coordinator"})
                issue_id(setup["issue_id"])
                token(setup["issue_uuid"])
                # Prepare the register values for the next contract boundary.
                key = participant_key(request)
                # Enforce NOT_OWNER, SCOPE_MISSING boundaries.
                require(setup["coordinator"] == key, "NOT_OWNER")
                validate_packet(setup["packet"])
                require(all(ref["reader"] == key for ref in setup["packet"]), "SCOPE_MISSING")
                # Keep filesystem resources scoped to this read or publication phase.
                with local.child(".bindings", True) as bindings:
                    # Prepare the register values for the next contract boundary.
                    name = key + ".startup.json"
                    # Validate any existing entry before reusing or replacing it.
                    if bindings.exists(name):
                        require(bindings.json(name) == setup, "BINDING_CONFLICT")
                    # Publish validated content and flush the required filesystem boundary.
                    bindings.put(name, setup)
    return {"ok": True, "code": "REGISTERED", **identity}


class Store:
    """Own validated descriptors connecting one worktree to its shared task store.
    """
    def __init__(self, request):
        """Open the registered shared store and verify all repository identities.

        Args:
            request: Versioned lifecycle request with explicit repository, issue and session identities.

        Raises:
            WorkspaceError: If local and canonical registration or filesystem identities differ.
            OSError: If a store directory cannot be opened.
        """
        # Build the request from explicit caller or observed session identities.
        self.request = request
        self.root, common, trees = repository(request["worktree"])
        self.handles = []
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            # Prepare the __init__ values for the next contract boundary.
            self.local_root = self.keep(Directory.absolute(self.root))
            self.local = self.keep(self.local_root.child(".task"))
            # Read repository registration through validated directory handles.
            self.registration = self.local.json(".repository.json")
            # Prepare the __init__ values for the next contract boundary.
            reg = self.registration
            # Enforce REPOSITORY_MISMATCH boundaries.
            require(request.get("repo_id") == reg["repo_id"], "REPOSITORY_MISMATCH")
            require(str(common) == reg["common"] and fs_identity(common) == reg["common_identity"]
                    and Path(reg["main"]) == trees[0], "REPOSITORY_MISMATCH")
            # Prepare the __init__ values for the next contract boundary.
            self.main = self.keep(Directory.absolute(reg["main"]))
            # Enforce REPOSITORY_MISMATCH boundaries.
            require(self.main.identity == reg["main_identity"], "REPOSITORY_MISMATCH")
            # Prepare the __init__ values for the next contract boundary.
            self.task = self.keep(self.main.child(".task"))
            self.control = self.keep(self.task.child(".control"))
            # Enforce REPOSITORY_MISMATCH boundaries.
            require(self.control.json("repository.json") == reg, "REPOSITORY_MISMATCH")
            # Prepare the __init__ values for the next contract boundary.
            self.issues = self.keep(self.control.child("issues", True))
            self.bindings = self.keep(self.local.child(".bindings", True))
        except BaseException:
            # Preserve or translate this failure according to the enclosing contract.
            self.close()
            raise

    def keep(self, directory):
        """Track a descriptor for reverse-order store cleanup.

        Args:
            directory: Opened Directory handle for the relevant payload or store.

        Returns:
            The supplied directory handle.
        """
        # Prepare the keep values for the next contract boundary.
        self.handles.append(directory)
        return directory

    def close(self):
        """Release all tracked descriptors in reverse opening order.

        Raises:
            OSError: If a tracked descriptor cannot be closed.
        """
        # Process each directory under the same validation boundary.
        for directory in reversed(self.handles):
            directory.close()
        # Prepare the close values for the next contract boundary.
        self.handles = []

    def __enter__(self):
        """Expose the validated store within a managed lifetime.

        Returns:
            This store.
        """
        return self

    def __exit__(self, *args):
        """Release store descriptors when the managed lifetime ends.

        Args:
            args: Context-manager exception triple, ignored during descriptor cleanup.

        Raises:
            OSError: If descriptor cleanup fails.
        """
        # Release the scoped resource or remove the already validated direct entry.
        self.close()

    def binding(self):
        """Read this host/session binding without inventing an assignment.

        Returns:
            The binding object, or None when it does not exist.

        Raises:
            WorkspaceError: If stored binding data is unsafe or malformed.
            OSError: If binding access fails.
        """
        # Prepare the binding values for the next contract boundary.
        name = participant_key(self.request) + ".json"
        return self.bindings.json(name) if self.bindings.exists(name) else None

    def save_binding(self, state, participant):
        """Persist the committed issue and participant generation for this worktree.

        Args:
            state: Current or staged issue control state.
            participant: Participant key whose committed generation is recorded.

        Raises:
            WorkspaceError: If binding publication violates file safety checks.
            OSError: If the binding cannot be persisted.
        """
        # Publish validated content and flush the required filesystem boundary.
        self.bindings.put(participant + ".json", {
            "repo_id": self.registration["repo_id"], "issue_id": state["issue_id"],
            "participant_id": participant, "binding_generation": state["participants"][participant]["generation"]})

    def view(self, issue):
        """Expose only the assigned canonical issue through a verified worktree link.

        Args:
            issue: Validated issue ID whose canonical payload must be exposed.

        Raises:
            WorkspaceError: If an existing view targets an unexpected location.
            OSError: If the link or canonical directory cannot be accessed.
        """
        # Handle the case str(self.root) == self.registration['main'].
        if str(self.root) == self.registration["main"]:
            return
        # Prepare the view values for the next contract boundary.
        target = str(Path(self.registration["main"]) / ".task" / issue)
        # Validate any existing entry before reusing or replacing it.
        if not self.local.exists(issue):
            # Attempt this phase while retaining its error and cleanup paths.
            try:
                # Prepare the view values for the next contract boundary.
                os.symlink(target, issue, dir_fd=self.local.fd)
                # Publish validated content and flush the required filesystem boundary.
                os.fsync(self.local.fd)
            except FileExistsError:
                # Preserve or translate this failure according to the enclosing contract.
                pass
        # Prepare the view values for the next contract boundary.
        s = os.stat(issue, dir_fd=self.local.fd, follow_symlinks=False)
        # Enforce UNSAFE_PATH boundaries.
        require(stat.S_ISLNK(s.st_mode) and os.readlink(issue, dir_fd=self.local.fd) == target,
                "UNSAFE_PATH")
        # Keep filesystem resources scoped to this read or publication phase.
        with self.task.child(issue):
            pass


def participant_key(request):
    """Derive a stable participant key from explicit host and session tokens.

    Args:
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The SHA-256 identity key.

    Raises:
        WorkspaceError: If either identity token is invalid.
    """
    return sha(canonical([token(request.get("host", "codex")), token(request["session_id"])]))


def os_metadata(name):
    """Recognize only Finder metadata names excluded from task payload content.

    Args:
        name: Single issue-root or context entry name, not a path.

    Returns:
        Whether the name is .DS_Store or a nonempty AppleDouble name.
    """
    return name == ".DS_Store" or (name.startswith("._") and len(name) > 2)


def inventory(directory, require_roadmap=True):
    """Read supported payload files while safely validating excluded OS metadata.

    Args:
        directory: Opened Directory handle for the relevant payload or store.
        require_roadmap: Whether an absent roadmap must stop inventory loading.

    Returns:
        Relative payload paths mapped to exact bytes.

    Raises:
        WorkspaceError: If a payload path, metadata file or required roadmap is unsafe.
        OSError: If enumeration or file access fails.
    """
    # Prepare the inventory values for the next contract boundary.
    result = {}
    # Inspect only direct issue entries under the opened directory descriptor.
    for name in os.listdir(directory.fd):
        # Validate narrow metadata entries before excluding them from task content.
        if os_metadata(name):
            # Read inventory inputs through the scoped file interface.
            directory.read(name, MAX_FILE)
            # Prepare the inventory values for the next contract boundary.
            continue
        # Handle the context hook event.
        if name == "context":
            # Keep filesystem resources scoped to this read or publication phase.
            with directory.child(name, private=False) as context:
                # Validate each direct context entry without descending into arbitrary directories.
                for note in os.listdir(context.fd):
                    # Validate narrow metadata entries before excluding them from task content.
                    if os_metadata(note):
                        # Read inventory inputs through the scoped file interface.
                        context.read(note, MAX_FILE)
                        # Prepare the inventory values for the next contract boundary.
                        continue
                    # Enforce the explicit identity and input contract.
                    path = payload_path("context/" + note)
                    # Read inventory inputs through the scoped file interface.
                    result[path] = context.read(note, MAX_FILE)
        else:
            # Enforce the explicit identity and input contract.
            payload_path(name, events=True)
            # Read inventory inputs through the scoped file interface.
            result[name] = directory.read(name, MAX_FILE)
    # Enforce RECOVERY_REQUIRED boundaries.
    require(not require_roadmap or "roadmap.md" in result, "RECOVERY_REQUIRED")
    return result


def manifest(files):
    """Derive a stable path-to-digest inventory from exact payload bytes.

    Args:
        files: Relative payload paths mapped to exact bytes.

    Returns:
        Sorted relative paths mapped to SHA-256 digests.
    """
    return {path: sha(data) for path, data in sorted(files.items())}


def write_payload(directory, path, data):
    """Publish one validated root or context payload file.

    Args:
        directory: Opened Directory handle for the relevant payload or store.
        path: Path to validate or access within the stated filesystem boundary.
        data: Exact input bytes to inspect or transform.

    Raises:
        WorkspaceError: If the payload path or destination is unsafe.
        OSError: If directory creation or file publication fails.
    """
    # Enforce the explicit identity and input contract.
    payload_path(path, events=True)
    # Route context writes through the opened context directory, not arbitrary traversal.
    if path.startswith("context/"):
        # Keep filesystem resources scoped to this read or publication phase.
        with directory.child("context", True, private=False) as context:
            context.write(path[8:], data)
    else:
        # Publish validated content and flush the required filesystem boundary.
        directory.write(path, data)


def validate_events(data, seq=0, prior=None):
    """Validate complete event lines against sequence and digest-chain anchors.

    Args:
        data: Exact input bytes to inspect or transform.
        seq: Sequence immediately before these lines, or zero for initial history.
        prior: Digest immediately before these lines, or None at history start.

    Returns:
        The final sequence number and digest.

    Raises:
        WorkspaceError: If a line, sequence or chain digest is invalid.
    """
    # Enforce INTEGRITY_ERROR boundaries.
    require(not data or data.endswith(b"\n"), "INTEGRITY_ERROR")
    # Validate every complete event against the expected sequence and prior digest.
    for line in data.splitlines():
        # Enforce INTEGRITY_ERROR boundaries.
        require(len(line) <= 8192, "INTEGRITY_ERROR")
        # Prepare sequence and event values without dropping prior history.
        event = strict_json(line)
        digest = event.pop("digest", None)
        # Enforce INTEGRITY_ERROR boundaries.
        require(event["seq"] == seq + 1 and event["prev_digest"] == prior and
                sha(canonical(event)) == digest, "INTEGRITY_ERROR")
        # Prepare sequence and event values without dropping prior history.
        prior, seq = digest, seq + 1
    return seq, prior


def validate_history(files):
    """Validate retained segments and the active stream as one continuous chain.

    Args:
        files: Relative payload paths mapped to exact bytes.

    Returns:
        The global final sequence number and digest.

    Raises:
        WorkspaceError: If segment ranges or event-chain integrity fail.
    """
    # Prepare sequence and event values without dropping prior history.
    seq, head = 0, None
    # Validate retained segment ranges before continuing into the active stream.
    for path in sorted(p for p in files if SEGMENT.fullmatch(p)):
        # Prepare the validate_history values for the next contract boundary.
        first, last = map(int, SEGMENT.fullmatch(path).groups())
        # Enforce INTEGRITY_ERROR boundaries.
        require(first == seq + 1 and last >= first, "INTEGRITY_ERROR")
        # Prepare sequence and event values without dropping prior history.
        seq, head = validate_events(files[path], seq, head)
        # Enforce INTEGRITY_ERROR boundaries.
        require(seq == last, "INTEGRITY_ERROR")
    return validate_events(files.get("events.jsonl", b""), seq, head)


class Issue:
    """Coordinate one issue payload with its recoverable control state.
    """
    def __init__(self, store, control, identifier):
        """Load the current issue state from its validated control directory.

        Args:
            store: Validated shared repository Store.
            control: Opened control Directory for this issue.
            identifier: Validated issue ID or explicit tool-call identity.

        Raises:
            WorkspaceError: If existing state is unsafe or invalid JSON.
            OSError: If state access fails.
        """
        # Prepare the __init__ values for the next contract boundary.
        self.store, self.control, self.id = store, control, identifier
        # Read __init__ inputs through the scoped file interface.
        self.state = control.json("state.json") if control.exists("state.json") else None

    def files(self):
        """Read the committed payload and quarantine only a provable interrupted tail.

        Returns:
            Validated payload paths and exact bytes.

        Raises:
            WorkspaceError: If identity, manifest or event integrity fails.
            OSError: If payload access or tail quarantine fails.
        """
        # Enforce RECOVERY_REQUIRED boundaries.
        require(self.state and self.state["storage"] != "cleaned", "RECOVERY_REQUIRED")
        # Keep filesystem resources scoped to this read or publication phase.
        with self.store.task.child(self.id, private=False) as payload:
            # Enforce UNSAFE_PATH boundaries.
            require(payload.identity == self.state["directory_identity"], "UNSAFE_PATH")
            # Prepare the files values for the next contract boundary.
            result = inventory(payload)
        # Reconcile only an interrupted tail that reproduces the exact committed manifest.
        if manifest(result) != self.state["files"]:
            # Prepare sequence and event values without dropping prior history.
            data = result.get("events.jsonl", b"")
            end = data.rfind(b"\n") + 1
            prefix, tail = data[:end], data[end:]
            corrected = {**result, "events.jsonl": prefix}
            # Quarantine only uncommitted tail bytes; never truncate an altered committed prefix.
            if tail and manifest(corrected) == self.state["files"]:
                # Publish validated content and flush the required filesystem boundary.
                self.control.write("interrupted-tail-" + sha(tail), tail)
                # Keep filesystem resources scoped to this read or publication phase.
                with self.store.task.child(self.id, private=False) as payload:
                    payload.write("events.jsonl", prefix)
                # Prepare the files values for the next contract boundary.
                result = corrected
                # Publish validated content and flush the required filesystem boundary.
                self.control.put("diagnostic-loss.json", {"discarded_tail_bytes": len(tail), "at": now()})
            else:
                # Stop with the original failure rather than continue with incomplete state.
                raise WorkspaceError("UNTRACKED_CHANGE")
        # Prepare sequence and event values without dropping prior history.
        seq, head = validate_history(result)
        # Enforce INTEGRITY_ERROR boundaries.
        require(seq == self.state["seq"] and head == self.state["head"], "INTEGRITY_ERROR")
        return result

    def recover(self):
        """Roll a durable transaction forward only over old or intended file bytes.

        Raises:
            WorkspaceError: If recovery encounters foreign content or inconsistent identity.
            OSError: If publication or durable cleanup fails.
        """
        # Skip recovery when no durable transaction intent exists.
        if not self.control.exists("transaction.json"):
            return
        # Read recover inputs through the scoped file interface.
        tx = self.control.json("transaction.json")
        # Prepare the recover values for the next contract boundary.
        state = tx["state"]
        # Before any publication, prove every file is either its old or staged version.
        if not self.store.task.exists(self.id):
            # Enforce RECOVERY_REQUIRED boundaries.
            require(tx["base"] is None, "RECOVERY_REQUIRED")
            # Keep filesystem resources scoped to this read or publication phase.
            with self.store.task.child(self.id, True, private=False):
                pass
        # Keep filesystem resources scoped to this read or publication phase.
        with self.store.task.child(self.id, private=False) as payload:
            # Handle the case tx['base'] is not None.
            if tx["base"] is not None:
                require(payload.identity == tx["directory_identity"], "UNSAFE_PATH")
            # Prepare the recover values for the next contract boundary.
            present = inventory(payload, require_roadmap=False)
            # Enforce UNTRACKED_CHANGE boundaries.
            require(set(present) <= set(state["files"]), "UNTRACKED_CHANGE")
            # Prove each present file matches either the old or intended transaction digest.
            for path, data in present.items():
                # Prepare the recover values for the next contract boundary.
                old = (tx["base"] or {}).get(path)
                # Enforce UNTRACKED_CHANGE boundaries.
                require(sha(data) in {old, state["files"][path]}, "UNTRACKED_CHANGE")
            # Publish staged files through the same safe payload writer used by normal commits.
            for path, data in tx["writes"].items():
                # Prepare the recover values for the next contract boundary.
                write_payload(payload, path, decode(data))
                fault("payload:" + path)
            # Release the scoped resource or remove the already validated direct entry.
            payload.child("context", True, private=False).close()
            # Enforce RECOVERY_REQUIRED boundaries.
            require(manifest(inventory(payload)) == state["files"], "RECOVERY_REQUIRED")
            # Stage the verified lifecycle changes in the issue state.
            state["directory_identity"] = payload.identity
            # Publish validated content and flush the required filesystem boundary.
            os.fsync(payload.fd)
        # Prepare the recover values for the next contract boundary.
        fault("payload-flush")
        # Publish validated content and flush the required filesystem boundary.
        self.control.put("state.json", state)
        # Prepare the recover values for the next contract boundary.
        fault("state")
        # Release the scoped resource or remove the already validated direct entry.
        self.control.unlink("transaction.json")
        # Prepare the recover values for the next contract boundary.
        fault("transaction-complete")
        self.state = state

    def commit(self, state, files, request, result, event_type=None):
        """Commit payload, event, state and idempotency result through one recovery intent.

        Args:
            state: Current or staged issue control state.
            files: Relative payload paths mapped to exact bytes.
            request: Versioned lifecycle request with explicit repository, issue and session identities.
            result: Operation result to persist with its idempotency record.
            event_type: Required lifecycle fact, optional observation, or None for no event.

        Returns:
            The committed operation result.

        Raises:
            WorkspaceError: If capacity, request or prior-payload checks fail.
            OSError: If the transaction cannot be durably published or recovered.
        """
        # Validate the old committed inventory before staging any replacement bytes.
        old_files = self.files() if self.state else {}
        # Handle the case event_type.
        if event_type:
            # Enforce INVALID_REQUEST boundaries.
            require(request.get("event") is None or request["operation"] == "event", "INVALID_REQUEST")
            # Stage the participant's pending-operation state for the next transaction.
            optional = event_type == "observation"
            # Reserve async-transition and completion events for every admitted pending tool.
            # Reserve async-transition and completion events for every admitted pending tool.
            pending_slots = sum(1 if tool["status"] == "unknown" else 2
                                for member in state["participants"].values()
                                for tool in member["pending"].values())
            reserved = 8192 * (1 + pending_slots)
            # Suppress optional observations before they consume settlement reserves.
            if optional and len(files.get("events.jsonl", b"")) + reserved + 8192 > MAX_EVENTS:
                # Stage the verified lifecycle changes in the issue state.
                state["optional_loss_count"] = state.get("optional_loss_count", 0) + 1
                result["code"] = "OPTIONAL_SUPPRESSED"
                return self.commit(state, files, request, result)
            # Advance the revision and build one bounded fact chained to the retained head.
            state["revision"] += 1
            event = {"schema_version": 1, "event_id": str(uuid.uuid4()),
                     "seq": state["seq"] + 1, "at": now(),
                     "repo_id": state["repo_id"], "issue_id": self.id,
                     "participant_id": participant_key(request),
                     "generation": state["participants"].get(participant_key(request), {}).get("generation", 0),
                     "type": event_type, "outcome": "ok", "required": not optional,
                     "operation_id": sha(request["request_id"].encode()),
                     "revision": state["revision"], "cause_id": None, "refs": [],
                     "code": request.get("event", {}).get("code", "OK"), "summary": event_type, "prev_digest": state["head"]}
            event["digest"] = sha(canonical(event))
            line = canonical(event) + b"\n"
            # Enforce SIZE_LIMIT boundaries.
            require(len(line) <= 8192, "SIZE_LIMIT")
            # Prepare sequence and event values without dropping prior history.
            files["events.jsonl"] = files.get("events.jsonl", b"") + line
            # Keep enough room to freeze a provider checkpoint at the boundary.
            limit = MAX_EVENTS if event_type == "archive-prepare" else MAX_EVENTS - reserved
            # Enforce ARCHIVE_PENDING boundaries.
            require(len(files["events.jsonl"]) <= limit, "ARCHIVE_PENDING")
            # Stage the verified lifecycle changes in the issue state.
            state["seq"], state["head"] = event["seq"], event["digest"]
            state.pop("archive", None)
        # Bind the next state and response to the exact intended payload inventory.
        state["files"] = manifest(files)
        result.update(ok=True, code=result.get("code", "OK"), revision=state["revision"],
                      repo_id=state["repo_id"], issue_id=self.id)
        stored_result = result
        # Store an immutable snapshot reference for archive-prepare retries.
        if "parts" in result and "snapshot" in result and request["operation"] == "archive-prepare":
            # Prepare the commit values for the next contract boundary.
            stored_result = {k: v for k, v in result.items() if k != "parts"}
            stored_result["result_ref"] = "export-" + result["snapshot"] + ".json"
        # Stage the verified lifecycle changes in the issue state.
        state["requests"][sha(request["request_id"].encode())] = {
            "digest": sha(canonical(request)), "result": stored_result}
        # Enforce ARCHIVE_PENDING boundaries.
        require(len(canonical(state)) <= 8 * 1024 * 1024, "ARCHIVE_PENDING")
        # Stage the verified lifecycle changes in the issue state.
        tx = {"base": self.state["files"] if self.state else None,
              "directory_identity": self.state.get("directory_identity") if self.state else None,
              "state": state, "writes": {p: encode(b) for p, b in files.items() if old_files.get(p) != b}}
        # Enforce SIZE_LIMIT boundaries.
        require(len(canonical(tx)) <= MAX_REQUEST, "SIZE_LIMIT")
        # Publish the durable recovery intent before mutating payload or state files.
        fault("before-intent")
        # Publish validated content and flush the required filesystem boundary.
        self.control.put("transaction.json", tx)
        # Prepare the commit values for the next contract boundary.
        fault("intent")
        self.recover()
        return result


def authorize(state, request, coordinator=False, maintenance=False):
    """Require a current participant generation and any requested coordinator authority.

    Args:
        state: Current or staged issue control state.
        request: Versioned lifecycle request with explicit repository, issue and session identities.
        coordinator: Whether the caller must own coordinator authority.
        maintenance: Whether an explicit maintenance binding may authorize this operation.

    Returns:
        The participant key and participant state.

    Raises:
        WorkspaceError: If binding, generation, status or ownership is invalid.
    """
    # Stage the verified lifecycle changes in the issue state.
    key = participant_key(request)
    participant = state["participants"].get(key)
    # Enforce BINDING_MISSING, STALE_BINDING, NOT_OWNER boundaries.
    require(participant is not None, "BINDING_MISSING")
    require(request.get("binding_generation") == participant["generation"], "STALE_BINDING")
    require(participant["status"] in ({"attached", "ready", "maintenance"} if maintenance else
                                      {"attached", "ready"}), "STALE_BINDING")
    require(not coordinator or state["coordinator"] == key, "NOT_OWNER")
    return key, participant


def expected(state, request):
    """Require the caller to name the current issue revision.

    Args:
        state: Current or staged issue control state.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Raises:
        WorkspaceError: If the expected revision is stale or absent.
    """
    # Enforce REVISION_CONFLICT boundaries.
    require(request.get("expected_revision") == state["revision"], "REVISION_CONFLICT")


def evidence(value):
    """Validate a bounded list of attributable evidence references.

    Args:
        value: Value to validate or encode under this helper's contract.

    Returns:
        The validated reference list.

    Raises:
        WorkspaceError: If evidence fields, locators or digests are invalid.
    """
    # Enforce EVIDENCE_REQUIRED boundaries.
    require(isinstance(value, list) and 0 < len(value) <= 16, "EVIDENCE_REQUIRED")
    # Process each item under the same validation boundary.
    for item in value:
        # Enforce EVIDENCE_REQUIRED boundaries.
        require(isinstance(item, dict) and set(item) == {"id", "locator", "sha256"}, "EVIDENCE_REQUIRED")
        token(item["id"])
        require(isinstance(item["locator"], str) and 0 < len(item["locator"]) <= 2048,
                "EVIDENCE_REQUIRED")
        require(DIGEST.fullmatch(item["sha256"]), "EVIDENCE_REQUIRED")
    return value


def validate_packet(packet):
    """Validate explicit reader-scoped references without expanding their contents.

    Args:
        packet: Explicit reader-scoped source references.

    Returns:
        The validated packet.

    Raises:
        WorkspaceError: If packet shape, reference uniqueness or path rules fail.
    """
    # Enforce SCOPE_MISSING boundaries.
    require(isinstance(packet, list) and len(packet) <= 64, "SCOPE_MISSING")
    # Prepare the validate_packet values for the next contract boundary.
    identifiers = set()
    # Process each ref under the same validation boundary.
    for ref in packet:
        # Enforce SCOPE_MISSING boundaries.
        require(isinstance(ref, dict) and set(ref) == {"id", "locator", "sha256", "required",
                "authority", "reason", "stage", "reader"}, "SCOPE_MISSING")
        token(ref["id"])
        require(ref["id"] not in identifiers and DIGEST.fullmatch(ref["sha256"]), "SCOPE_MISSING")
        # Prepare the validate_packet values for the next contract boundary.
        identifiers.add(ref["id"])
        # Enforce SCOPE_MISSING boundaries.
        require(type(ref["required"]) is bool, "SCOPE_MISSING")
        # Process each field under the same validation boundary.
        for field in ("authority", "reason", "stage", "reader"):
            token(ref[field])
        # Prepare the validate_packet values for the next contract boundary.
        locator = ref["locator"]
        # Handle the case not Path(locator).is_absolute().
        if not Path(locator).is_absolute():
            payload_path(locator, events=True)
    return packet


def packet_reads(state, participant, files):
    """Check only assigned references and fail on missing or stale required sources.

    Args:
        state: Current or staged issue control state.
        participant: Participant state containing the assigned packet.
        files: Relative payload paths mapped to exact bytes.

    Returns:
        Assigned reference descriptors annotated with availability.

    Raises:
        WorkspaceError: If the packet is missing or a required source is stale.
    """
    # Enforce SCOPE_MISSING boundaries.
    require(participant.get("packet") is not None, "SCOPE_MISSING")
    # Prepare the explicit source references for scoped validation.
    refs = []
    # Process each ref under the same validation boundary.
    for ref in participant["packet"]:
        # Prepare the packet_reads values for the next contract boundary.
        data = None
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            # Handle the case Path(ref['locator']).is_absolute().
            if Path(ref["locator"]).is_absolute():
                # Prepare the packet_reads values for the next contract boundary.
                path = Path(ref["locator"])
                # Keep filesystem resources scoped to this read or publication phase.
                with Directory.absolute(path.parent) as parent:
                    data = parent.read(path.name, MAX_FILE)
            else:
                # Prepare the packet_reads values for the next contract boundary.
                data = files.get(ref["locator"])
        except (OSError, WorkspaceError):
            # Preserve or translate this failure according to the enclosing contract.
            if ref["required"]:
                raise WorkspaceError("SOURCE_STALE") from None
        # Prepare the packet_reads values for the next contract boundary.
        valid = data is not None and sha(data) == ref["sha256"]
        # Enforce SOURCE_STALE boundaries.
        require(valid or not ref["required"], "SOURCE_STALE")
        # Prepare the explicit source references for scoped validation.
        refs.append({**ref, "available": valid})
    return refs


def new_state(store, request):
    """Construct initial issue state for its explicitly identified coordinator.

    Args:
        store: Validated shared repository Store.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        An uncommitted initial state object.

    Raises:
        WorkspaceError: If coordinator or issue identity is invalid.
    """
    # Prepare the new_state values for the next contract boundary.
    key = participant_key(request)
    # Enforce NOT_OWNER boundaries.
    require(request.get("coordinator") == key, "NOT_OWNER")
    return {"schema_version": 1, "repo_id": store.registration["repo_id"],
            "issue_id": request["issue_id"], "issue_uuid": token(request["issue_uuid"]),
            "revision": 0, "generation": 1, "disposition": "active", "storage": "present",
            "coordinator": key, "owners": {"roadmap.md": key}, "participants": {},
            "files": {}, "seq": 0, "head": None, "requests": {}, "provenance": {}}


def attach(state, request):
    """Attach or resume an assigned participant without resetting existing task content.

    Args:
        state: Current or staged issue control state.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The participant identity and current binding generation.

    Raises:
        WorkspaceError: If disposition, storage, assignment or generation forbids attachment.
    """
    # Prepare the attach values for the next contract boundary.
    key = participant_key(request)
    # Enforce RECOVERY_REQUIRED boundaries.
    require(state["disposition"] not in TERMINAL and state["storage"] == "present", "RECOVERY_REQUIRED")
    # Stage the verified lifecycle changes in the issue state.
    participant = state["participants"].get(key)
    # Handle the case participant.
    if participant:
        # Stage the verified lifecycle changes in the issue state.
        initial_create = (request["operation"] == "create" and request.get("binding_generation") is None
                          and participant["generation"] == 1 and key == state["coordinator"])
        # Enforce STALE_BINDING boundaries.
        require(initial_create or request.get("binding_generation") == participant["generation"], "STALE_BINDING")
        require(participant["status"] in {"attached", "ready", "detached"}, "STALE_BINDING")
        # Handle the case participant['status'] == 'detached'.
        if participant["status"] == "detached":
            # Prepare the attach values for the next contract boundary.
            participant["generation"] += 1
            participant["status"] = "attached"
            participant["ack"] = None
    else:
        # Enforce SCOPE_MISSING boundaries.
        require(key == state["coordinator"] or key in state.get("assignments", {}), "SCOPE_MISSING")
        # Stage the participant's pending-operation state for the next transaction.
        assignment = state.get("assignments", {}).get(key, {})
        participant = {"generation": 1, "status": "attached", "pending": {},
                       "packet": assignment.get("packet"), "ack": None}
        state["participants"][key] = participant
    return {"participant_id": key, "binding_generation": participant["generation"]}


def archive_payload(state, files):
    """Freeze reconstructable payload bytes and their provenance into an archive value.

    Args:
        state: Current or staged issue control state.
        files: Relative payload paths mapped to exact bytes.

    Returns:
        The versioned snapshot, including segment lineage when present.
    """
    # Stage the verified lifecycle changes in the issue state.
    result = {"schema_version": 1, "repo_id": state["repo_id"], "issue_id": state["issue_id"],
            "issue_uuid": state["issue_uuid"], "revision": state["revision"],
            "disposition": state["disposition"], "outcome": state.get("outcome"),
            "checkpoint": state.get("checkpoint"), "provenance": state.get("provenance", {}),
            "adoption": state.get("adoption"), "checkpoint_history": state.get("checkpoint_history", []),
            "outcome_history": state.get("outcome_history", []),
            "seq": state["seq"], "head": state["head"],
            "files": [{"path": p, "size": len(b), "sha256": sha(b), "scope": "coordinator-archive",
                       "data": encode(b)} for p, b in sorted(files.items())]}
    # Handle the case state.get('event_segments').
    if state.get("event_segments"):
        result["event_segments"] = state["event_segments"]
    return result


def document(payload, heading):
    """Wrap a structured archive value in a readable Markdown document.

    Args:
        payload: Structured value to embed in the provider document.
        heading: Readable Markdown heading and explanatory text.

    Returns:
        Markdown containing one canonical fenced JSON value.
    """
    return heading + "\n\n```json\n" + canonical(payload).decode() + "\n```\n"


def parse_document(content):
    """Extract exactly one bounded structured value from provider Markdown.

    Args:
        content: Provider Markdown containing a single structured archive block.

    Returns:
        The decoded fenced JSON value.

    Raises:
        WorkspaceError: If size, fenced-block count or JSON validation fails.
    """
    # Enforce SIZE_LIMIT boundaries.
    require(isinstance(content, str) and len(content.encode()) <= DOCUMENT_LIMIT, "SIZE_LIMIT")
    # Prepare the parse_document values for the next contract boundary.
    matches = re.findall(r"^```json[ \t]*\n(.*?)\n```[ \t]*$", content, re.S | re.M)
    # Enforce INTEGRITY_ERROR boundaries.
    require(len(matches) == 1, "INTEGRITY_ERROR")
    return strict_json(matches[0])


def export_documents(snapshot):
    """Split a frozen snapshot into bounded readable, reconstructable provider parts.

    Args:
        snapshot: Frozen versioned issue snapshot and reconstructable file bytes.

    Returns:
        The snapshot digest and ordered document requests.

    Raises:
        WorkspaceError: If snapshot or document capacity is exceeded.
    """
    # Prepare the export_documents values for the next contract boundary.
    raw = canonical(snapshot)
    # Enforce ARCHIVE_PENDING boundaries.
    require(len(raw) <= MAX_ARCHIVE, "ARCHIVE_PENDING")
    # Prepare the export_documents values for the next contract boundary.
    digest = sha(raw)
    # Byte chunks, not file splitting, bound even one large file.
    chunks = [raw[i:i + ARCHIVE_CHUNK] for i in range(0, len(raw), ARCHIVE_CHUNK)]
    readable = "\n\n".join("File: " + item["path"] + "\n" + decode(item["data"]).decode("utf-8")
                               for item in snapshot["files"])
    width = max(1, (len(readable) + len(chunks) - 1) // len(chunks))
    parts = []
    # Process each (i, chunk) under the same validation boundary.
    for i, chunk in enumerate(chunks):
        # Prepare sequence and event values without dropping prior history.
        body = {"schema_version": 1, "kind": "part", "snapshot": digest, "index": i,
                "count": len(chunks), "sha256": sha(chunk), "data": encode(chunk)}
        fragment = readable[i * width:(i + 1) * width]
        heading = (f"# {snapshot['issue_id']} archive part {i + 1}/{len(chunks)}\n\n"
                   "## Readable history fragment\n\n" + "\n".join("> " + line for line in fragment.splitlines()))
        content = document(body, heading)
        # Enforce ARCHIVE_PENDING boundaries.
        require(len(content.encode()) <= DOCUMENT_LIMIT, "ARCHIVE_PENDING")
        # Prepare the export_documents values for the next contract boundary.
        parts.append({"title": f"{snapshot['issue_id']} snapshot {digest} part {i + 1}",
                      "issue": snapshot["issue_uuid"], "content": content})
    return {"snapshot": digest, "parts": parts}


def provider_observation(item, issue_uuid):
    """Validate the origin, parent and identity fields of a supplied document read-back.

    Args:
        item: One supplied independent provider document observation.
        issue_uuid: Exact provider parent identity required for the observation.

    Returns:
        The structured archive value from the observed content.

    Raises:
        WorkspaceError: If observation identity, shape or content is invalid.
    """
    # Enforce ARCHIVE_PENDING boundaries.
    require(isinstance(item, dict) and set(item) == {"id", "url", "issue", "updatedAt", "content",
                                                  "origin", "request_id"}, "ARCHIVE_PENDING")
    require(item["issue"] == issue_uuid and item["origin"] == "linear_get_document", "ARCHIVE_PENDING")
    # Process each field under the same validation boundary.
    for field in ("id", "updatedAt", "request_id"):
        token(item[field])
    # Enforce ARCHIVE_PENDING boundaries.
    require(isinstance(item["url"], str) and item["url"].startswith("https://linear.app/"), "ARCHIVE_PENDING")
    return parse_document(item["content"])


def verify_provider(state, observations):
    """Reconstruct a frozen export from exact ordered provider versions and digests.

    Args:
        state: Current or staged issue control state.
        observations: Independent provider read-backs for all parts and the root index.

    Returns:
        A tuple of verified snapshot, payload bytes and archive receipt.

    Raises:
        WorkspaceError: If parent, snapshot, parts, versions, digests or event history disagree.
    """
    # Enforce ARCHIVE_PENDING boundaries.
    require(isinstance(observations, list) and 2 <= len(observations) <= 514, "ARCHIVE_PENDING")
    # Stage the verified lifecycle changes in the issue state.
    parsed = [provider_observation(x, state["issue_uuid"]) for x in observations]
    roots = [(o, p) for o, p in zip(observations, parsed) if p.get("kind") == "index"]
    # Enforce ARCHIVE_PENDING boundaries.
    require(len(roots) == 1, "ARCHIVE_PENDING")
    # Prepare the verify_provider values for the next contract boundary.
    root_observed, root = roots[0]
    # Enforce ARCHIVE_PENDING boundaries.
    require(root.get("schema_version") == 1 and root["snapshot"] == state["export"]["snapshot"], "ARCHIVE_PENDING")
    # Prepare the verify_provider values for the next contract boundary.
    parts = {o["id"]: (o, p) for o, p in zip(observations, parsed) if p.get("kind") == "part"}
    # Enforce ARCHIVE_PENDING boundaries.
    require(len(parts) == len(observations) - 1 == len(root["parts"]) and
            root_observed["id"] not in parts, "ARCHIVE_PENDING")
    # Prepare the verify_provider values for the next contract boundary.
    raw = b""
    # Process each (i, descriptor) under the same validation boundary.
    for i, descriptor in enumerate(root["parts"]):
        # Enforce ARCHIVE_PENDING boundaries.
        require(descriptor["id"] in parts, "ARCHIVE_PENDING")
        # Prepare the verify_provider values for the next contract boundary.
        observed, part = parts[descriptor["id"]]
        # Enforce ARCHIVE_PENDING boundaries.
        require(part["schema_version"] == 1 and part["snapshot"] == root["snapshot"] and
                part["index"] == i and part["count"] == len(parts) and
                descriptor["sha256"] == sha(canonical(part)) and
                descriptor["updatedAt"] == observed["updatedAt"], "ARCHIVE_PENDING")
        # Prepare the verify_provider values for the next contract boundary.
        chunk = decode(part["data"], 128 * 1024)
        # Enforce INTEGRITY_ERROR boundaries.
        require(sha(chunk) == part["sha256"], "INTEGRITY_ERROR")
        # Prepare the verify_provider values for the next contract boundary.
        raw += chunk
        # Enforce SIZE_LIMIT boundaries.
        require(len(raw) <= MAX_ARCHIVE, "SIZE_LIMIT")
    # Enforce INTEGRITY_ERROR boundaries.
    require(sha(raw) == root["snapshot"], "INTEGRITY_ERROR")
    # Prepare the verify_provider values for the next contract boundary.
    snapshot = strict_json(raw)
    # Enforce INTEGRITY_ERROR boundaries.
    require(snapshot["schema_version"] == 1 and snapshot["repo_id"] == state["repo_id"] and
            snapshot["issue_id"] == state["issue_id"] and snapshot["issue_uuid"] == state["issue_uuid"],
            "INTEGRITY_ERROR")
    # Prepare the verify_provider values for the next contract boundary.
    files = {}
    # Process each item under the same validation boundary.
    for item in snapshot["files"]:
        # Enforce INTEGRITY_ERROR boundaries.
        path = payload_path(item["path"], True)
        require(path not in files, "INTEGRITY_ERROR")
        # Prepare the verify_provider values for the next contract boundary.
        data = decode(item["data"])
        # Enforce INTEGRITY_ERROR boundaries.
        require(len(data) == item["size"] and sha(data) == item["sha256"], "INTEGRITY_ERROR")
        # Prepare the verify_provider values for the next contract boundary.
        data.decode("utf-8")
        files[path] = data
    # Enforce INTEGRITY_ERROR boundaries.
    require("roadmap.md" in files and "events.jsonl" in files, "INTEGRITY_ERROR")
    require(validate_history(files) == (snapshot["seq"], snapshot["head"]), "INTEGRITY_ERROR")
    # Prepare the verify_provider values for the next contract boundary.
    receipt = {"snapshot": root["snapshot"], "root": {k: v for k, v in root_observed.items() if k != "content"},
               "parts": root["parts"], "read_at": now(), "observations_digest": sha(canonical(observations))}
    return snapshot, files, receipt


def eligible(state):
    """Require terminal evidence, no live work and a verified export before cleanup.

    Args:
        state: Current or staged issue control state.

    Raises:
        WorkspaceError: If participation, disposition or archive evidence requires retention.
    """
    # Enforce ARCHIVE_PENDING boundaries.
    require(state["disposition"] in TERMINAL and state.get("outcome"), "RETAINED")
    require(all(p["status"] in {"detached", "maintenance"} and not p["pending"]
                for p in state["participants"].values()), "RETAINED")
    require(state.get("export") and state.get("archive"), "ARCHIVE_PENDING")


def finish_cleanup(issue):
    """Finish only the recorded issue quarantine after validating its remaining tree.

    Args:
        issue: The issue handle or identifier to operate on.

    Raises:
        WorkspaceError: If identity, metadata safety or remaining payload digests differ.
        OSError: If quarantine, deletion or tombstone publication fails.
    """
    # Prepare the finish_cleanup values for the next contract boundary.
    control, task = issue.control, issue.store.task
    # Validate any existing entry before reusing or replacing it.
    if not control.exists("cleanup.json"):
        return
    # Read finish_cleanup inputs through the scoped file interface.
    intent = control.json("cleanup.json")
    # Prepare the finish_cleanup values for the next contract boundary.
    name = intent["quarantine"]
    # Validate any existing entry before reusing or replacing it.
    if task.exists(issue.id):
        # Enforce RECOVERY_REQUIRED boundaries.
        require(not control.exists(name), "RECOVERY_REQUIRED")
        # Keep filesystem resources scoped to this read or publication phase.
        with task.child(issue.id, private=False) as payload:
            require(payload.identity == intent["directory_identity"] and
                    manifest(inventory(payload)) == intent["files"], "UNTRACKED_CHANGE")
        # Publish validated content and flush the required filesystem boundary.
        os.rename(issue.id, name, src_dir_fd=task.fd, dst_dir_fd=control.fd)
        os.fsync(task.fd)
        os.fsync(control.fd)
        # Prepare the finish_cleanup values for the next contract boundary.
        fault("quarantine")
    # Validate any existing entry before reusing or replacing it.
    if control.exists(name):
        # Keep filesystem resources scoped to this read or publication phase.
        with control.child(name, private=False) as payload:
            # Enforce UNSAFE_PATH boundaries.
            require(payload.identity == intent["directory_identity"], "UNSAFE_PATH")
            # Read finish_cleanup inputs through the scoped file interface.
            names = os.listdir(payload.fd)
            # Enforce UNSAFE_PATH boundaries.
            require(all(n in {"roadmap.md", "events.jsonl", "context"} or SEGMENT.fullmatch(n) or os_metadata(n) for n in names), "UNSAFE_PATH")
            # Verify the ENTIRE remaining tree before deleting any more of it.
            remaining = {}
            # Read every remaining quarantine entry before deleting any payload bytes.
            for entry in names:
                # Handle the case entry == 'context'.
                if entry == "context":
                    # Keep filesystem resources scoped to this read or publication phase.
                    with payload.child("context", private=False) as context:
                        # Validate each direct context entry without descending into arbitrary directories.
                        for note in os.listdir(context.fd):
                            remaining["context/" + note] = context.read(note, MAX_FILE)
                else:
                    # Read finish_cleanup inputs through the scoped file interface.
                    remaining[entry] = payload.read(entry, MAX_FILE)
            # Enforce UNTRACKED_CHANGE boundaries.
            require(all(os_metadata(p.rsplit("/", 1)[-1]) or intent["files"].get(p) == sha(b)
                        for p, b in remaining.items()), "UNTRACKED_CHANGE")
            # Unlink only entries validated in the complete remaining quarantine inventory.
            for path in sorted(remaining):
                # Route context writes through the opened context directory, not arbitrary traversal.
                if path.startswith("context/"):
                    # Keep filesystem resources scoped to this read or publication phase.
                    with payload.child("context", private=False) as context:
                        context.unlink(path[8:])
                else:
                    # Release the scoped resource or remove the already validated direct entry.
                    payload.unlink(path)
                # Prepare the finish_cleanup values for the next contract boundary.
                fault("delete:" + path)
            # Validate any existing entry before reusing or replacing it.
            if payload.exists("context"):
                os.rmdir("context", dir_fd=payload.fd)
            # Publish validated content and flush the required filesystem boundary.
            os.fsync(payload.fd)
        # Release the scoped resource or remove the already validated direct entry.
        os.rmdir(name, dir_fd=control.fd)
        # Publish validated content and flush the required filesystem boundary.
        os.fsync(control.fd)
    # Stage the verified lifecycle changes in the issue state.
    state = intent["state"]
    state["storage"] = "cleaned"
    state["requests"] = {k: v for k, v in state["requests"].items()
                         if k == sha(intent["request_id"].encode())}
    state.pop("index_request", None)
    state.pop("checkpoint", None)
    state.pop("checkpoint_history", None)
    state.pop("outcome_history", None)
    state.pop("provenance", None)
    # Process each name under the same validation boundary.
    for name in os.listdir(control.fd):
        # Check the recorded archive state before allowing the next archive phase.
        if re.fullmatch(r"export-[0-9a-f]{64}\.json", name):
            # Read finish_cleanup inputs through the scoped file interface.
            control.read(name)  # Refuse substituted links/special files before pruning.
            # Release the scoped resource or remove the already validated direct entry.
            control.unlink(name)
    # Stage the verified lifecycle changes in the issue state.
    state["tombstone"] = {"snapshot": state["export"]["snapshot"], "at": now(),
                          "generation": state["generation"], "cleanup_request": intent["request_id"]}
    # Publish validated content and flush the required filesystem boundary.
    control.put("state.json", state)
    # Prepare the finish_cleanup values for the next contract boundary.
    fault("cleanup-state")
    # Release the scoped resource or remove the already validated direct entry.
    control.unlink("cleanup.json")
    # Prepare the finish_cleanup values for the next contract boundary.
    issue.state = state


def operate(store, issue, request):
    """Dispatch one issue request under the caller-held binding and issue locks.

    Args:
        store: Validated shared repository Store.
        issue: The issue handle or identifier to operate on.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The operation result, committed when mutation is required.

    Raises:
        WorkspaceError: If authorization, state, evidence or lifecycle constraints fail.
        OSError: If durable store operations fail.
    """
    # Prepare the operate values for the next contract boundary.
    operation = request["operation"]
    key = participant_key(request)
    issue.recover()
    finish_cleanup(issue)
    state = copy.deepcopy(issue.state)
    # Handle the case state.
    if state:
        # Enforce ISSUE_MISMATCH boundaries.
        require(not request.get("issue_uuid") or request["issue_uuid"] == state["issue_uuid"], "ISSUE_MISMATCH")
        # Stage the verified lifecycle changes in the issue state.
        previous = state["requests"].get(sha(request["request_id"].encode()))
        # Handle the case previous.
        if previous:
            # Enforce REQUEST_CONFLICT boundaries.
            require(previous["digest"] == sha(canonical(request)), "REQUEST_CONFLICT")
            # Enforce the shared lifecycle policy for this operation group.
            if operation in {"create", "adopt", "attach", "resume", "bind", "restore"} and state["storage"] != "cleaned":
                # Prepare the operate values for the next contract boundary.
                store.view(issue.id)
                # Persist the committed participant generation for this worktree.
                store.save_binding(state, key)
            # Prepare the operate values for the next contract boundary.
            result = previous["result"].copy()
            reference = result.pop("result_ref", None)
            # Handle the case reference.
            if reference:
                result.update(issue.control.json(reference))
            return result
    # Apply the diagnose transition.
    if operation == "diagnose":
        return {"ok": True, "code": "PRESENT" if state else "ABSENT", "repo_id": store.registration["repo_id"],
                "issue_id": issue.id, "revision": state["revision"] if state else None,
                "storage": state["storage"] if state else "absent"}
    # Read the existing session binding; never infer it from the prompt.
    binding = store.binding()
    # Enforce the shared lifecycle policy for this operation group.
    if operation in {"create", "adopt", "attach", "resume", "bind"}:
        require(not binding or binding["issue_id"] == issue.id, "BINDING_CONFLICT")
    # Handle the case not state.
    if not state:
        # Enforce BINDING_MISSING boundaries.
        require(operation in {"create", "adopt"}, "BINDING_MISSING")
        # Prepare the operate values for the next contract boundary.
        exists = store.task.exists(issue.id)
        # Enforce ADOPTION_REQUIRED, RECOVERY_REQUIRED boundaries.
        require(exists == (operation == "adopt"), "ADOPTION_REQUIRED" if exists else "RECOVERY_REQUIRED")
        # Prepare the operate values for the next contract boundary.
        state = new_state(store, request)
        # Apply the adopt transition.
        if operation == "adopt":
            # Enforce the explicit identity and input contract.
            evidence(request.get("evidence"))
            # Keep filesystem resources scoped to this read or publication phase.
            with store.task.child(issue.id, private=False) as payload:
                files = inventory(payload)
            # Enforce UNTRACKED_CHANGE boundaries.
            require(request.get("inventory") == manifest(files), "UNTRACKED_CHANGE")
            # Prepare the operate values for the next contract boundary.
            owners = request.get("owners", {})
            # Enforce NOT_OWNER boundaries.
            require(set(owners) == {p for p in files if p != "events.jsonl" and not SEGMENT.fullmatch(p)} and owners["roadmap.md"] == key,
                    "NOT_OWNER")
            require(all(DIGEST.fullmatch(v) for v in owners.values()), "NOT_OWNER")
            # Stage the verified lifecycle changes in the issue state.
            state["owners"] = owners
            state["adoption"] = {"inventory": manifest(files), "evidence": request["evidence"]}
            state["seq"], state["head"] = validate_history(files)
        else:
            # Prepare the operate values for the next contract boundary.
            template = Path(__file__).resolve().parents[2] / "core/templates/task-workspace/roadmap.md"
            # Read the event bytes needed to verify or preserve committed history.
            files = {"roadmap.md": template.read_text().replace("{{issue_id}}", issue.id).encode(),
                     "events.jsonl": b""}
        # Prepare the operate values for the next contract boundary.
        result = attach(state, request)
        # Publish this lifecycle result through the recoverable transaction.
        result = issue.commit(state, files, request, result, operation)
        # Prepare the operate values for the next contract boundary.
        store.view(issue.id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(issue.state, key)
        return result
    # Reconstruct only the tombstone-matching archive before reopening a new generation.
    if operation == "restore":
        # Enforce RECOVERY_REQUIRED, NOT_OWNER boundaries.
        require(state["storage"] == "cleaned" and state.get("tombstone"), "RECOVERY_REQUIRED")
        require(key == state["coordinator"], "NOT_OWNER")
        # Prepare the operate values for the next contract boundary.
        snapshot, files, receipt = verify_provider(state, request.get("observations"))
        # Enforce INTEGRITY_ERROR, RECOVERY_REQUIRED boundaries.
        require(receipt["snapshot"] == state["tombstone"]["snapshot"] and
                manifest(files) == state["files"], "INTEGRITY_ERROR")
        require(not store.task.exists(issue.id), "RECOVERY_REQUIRED")
        # Stage the verified lifecycle changes in the issue state.
        state["history"] = state.get("history", []) + [state["tombstone"]]
        state["storage"], state["disposition"] = "present", "active"
        state["provenance"] = snapshot.get("provenance", {})
        state["checkpoint"] = snapshot.get("checkpoint")
        state["event_segments"] = snapshot.get("event_segments", [])
        state["adoption"] = snapshot.get("adoption")
        state["checkpoint_history"] = snapshot.get("checkpoint_history", [])
        state["outcome_history"] = snapshot.get("outcome_history", [])
        state["generation"] += 1
        # Process each participant under the same validation boundary.
        for participant in state["participants"].values():
            participant.update(status="detached", ack=None, generation=participant["generation"] + 1)
        # Stage the verified lifecycle changes in the issue state.
        state["participants"][key]["status"] = "attached"
        state.pop("tombstone")
        # A durable initialization transaction restores the exact history before the reopen event.
        old = issue.state
        issue.state = None
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            result = issue.commit(state, files, request,
                                  {"binding_generation": state["participants"][key]["generation"]}, "restore")
        except BaseException:
            # Preserve or translate this failure according to the enclosing contract.
            issue.state = old
            raise
        # Prepare the operate values for the next contract boundary.
        store.view(issue.id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(issue.state, key)
        return result
    # Import inspected Markdown while refusing any change to committed event history.
    if operation == "reconcile-files":
        # Enforce BINDING_MISSING boundaries.
        require(state["disposition"] not in TERMINAL, "TERMINAL")
        require(binding and binding["issue_id"] == issue.id and
                binding["binding_generation"] == request.get("binding_generation"), "BINDING_MISSING")
        authorize(state, request, coordinator=True)
        expected(state, request)
        evidence(request.get("evidence"))
        # Keep filesystem resources scoped to this read or publication phase.
        with store.task.child(issue.id, private=False) as payload:
            # Enforce UNSAFE_PATH boundaries.
            require(payload.identity == state["directory_identity"], "UNSAFE_PATH")
            # Prepare the operate values for the next contract boundary.
            observed = inventory(payload)
        # Enforce UNTRACKED_CHANGE, INTEGRITY_ERROR boundaries.
        require(manifest(observed) == request.get("inventory"), "UNTRACKED_CHANGE")
        require(set(observed) == set(state["files"]) and
                all(sha(b) == state["files"][p] for p, b in observed.items()
                    if p == "events.jsonl" or SEGMENT.fullmatch(p)), "INTEGRITY_ERROR")
        # Explicit reconciliation imports inspected Markdown only, never arbitrary events.
        issue.state = copy.deepcopy(state)
        issue.state["files"] = manifest(observed)
        state["reconciliation"] = {"previous": state["files"], "evidence": request["evidence"]}
        return issue.commit(state, observed, request, {}, "reconciliation")
    # Prepare the operate values for the next contract boundary.
    files = issue.files()
    store.view(issue.id)
    # Enforce the shared lifecycle policy for this operation group.
    if operation in {"create", "attach", "resume", "bind"}:
        # Prepare the operate values for the next contract boundary.
        result = attach(state, request)
        # Publish this lifecycle result through the recoverable transaction.
        result = issue.commit(state, files, request, result, "attach")
        # Prepare the operate values for the next contract boundary.
        store.view(issue.id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(issue.state, key)
        return result
    # Enforce BINDING_MISSING boundaries.
    require(binding and binding["issue_id"] == issue.id and binding["binding_generation"] == request.get("binding_generation"),
            "BINDING_MISSING")
    # Prepare the operate values for the next contract boundary.
    maintenance = operation in {"archive-prepare", "archive-index", "archive-verify", "archive-save-start", "archive-observe-save", "cleanup-plan", "cleanup-commit", "reopen"}
    # Enforce the explicit identity and input contract.
    key, participant = authorize(state, request, maintenance=maintenance)
    # Prepare sequence and event values without dropping prior history.
    result = {}
    event_type = operation
    # Enforce the shared lifecycle policy for this operation group.
    if operation in {"scope", "update", "checkpoint", "outcome", "transfer-coordinator", "reconcile-participant",
                     "archive-prepare", "event-rollover", "reopen"}:
        # Stage the verified lifecycle changes in the issue state.
        prepared = (operation == "archive-prepare" and sha((request["request_id"] + ":prepare").encode()) in state["requests"])
        # Handle the case not prepared.
        if not prepared:
            expected(state, request)
    # Check the recorded archive state before allowing the next archive phase.
    if operation not in {"read", "ready", "diagnose", "archive-prepare", "archive-index", "archive-verify",
                         "cleanup-plan", "cleanup-commit", "detach", "reopen", "archive-save-start", "archive-observe-save"}:
        require(state["disposition"] not in TERMINAL, "TERMINAL")
    # Enforce the shared lifecycle policy for this operation group.
    if operation in {"scope", "checkpoint", "outcome", "transfer-coordinator", "reconcile-participant",
                     "archive-prepare", "archive-index", "archive-verify", "archive-save-start", "archive-observe-save", "cleanup-plan", "cleanup-commit", "event-rollover", "reopen"}:
        authorize(state, request, coordinator=True, maintenance=maintenance)
    # Apply the scope transition.
    if operation == "scope":
        # Prepare the operate values for the next contract boundary.
        target = request["target_participant"]
        # Enforce SCOPE_MISSING boundaries.
        require(DIGEST.fullmatch(target), "SCOPE_MISSING")
        packet = validate_packet(request["packet"])
        require(all(ref["reader"] == target for ref in packet), "SCOPE_MISSING")
        # Prepare the explicit source references for scoped validation.
        state.setdefault("assignments", {})[target] = {"packet": packet}
        # Update existing scope without reviving a detached generation.
        if target in state["participants"]:
            # Stage the verified lifecycle changes in the issue state.
            target_state = state["participants"][target]
            target_state.update(packet=packet, ack=None)
            # Handle the case target_state['status'] != 'detached'.
            if target_state["status"] != "detached":
                target_state["status"] = "attached"
        # Process each path under the same validation boundary.
        for path in request.get("owned_paths", []):
            # Enforce NOT_OWNER boundaries.
            payload_path(path)
            require(path != "roadmap.md" and state["owners"].get(path, target) == target, "NOT_OWNER")
            # Stage the verified lifecycle changes in the issue state.
            state["owners"][path] = target
        # Prepare the explicit source references for scoped validation.
        result["packet_digest"] = sha(canonical(packet))
    # Enforce the shared lifecycle policy for this operation group.
    elif operation in {"read", "acknowledge", "ready"}:
        # Prepare the explicit source references for scoped validation.
        refs = packet_reads(state, participant, files)
        digest = sha(canonical(participant["packet"]))
        # Apply the acknowledge transition.
        if operation == "acknowledge":
            # Enforce SOURCE_STALE boundaries.
            require(request.get("packet_digest") == digest, "SOURCE_STALE")
            # Prepare the operate values for the next contract boundary.
            participant.update(ack=digest, status="ready")
        # Apply the ready transition.
        elif operation == "ready":
            require(state["disposition"] not in TERMINAL and participant["ack"] == digest and
                    participant["status"] == "ready", "NOT_READY")
        # Handle the case operation != 'acknowledge'.
        if operation != "acknowledge":
            return {"ok": True, "code": "OK", "revision": state["revision"], "references": refs,
                    "packet_digest": digest, "binding_generation": participant["generation"]}
        # Prepare the explicit source references for scoped validation.
        result.update(references=refs, packet_digest=digest)
    # Apply the update transition.
    elif operation == "update":
        # Enforce NOT_OWNER, REVISION_CONFLICT boundaries.
        path = payload_path(request["path"])
        require(state["owners"].get(path) == key, "NOT_OWNER")
        require(request.get("old_digest") == state["files"].get(path), "REVISION_CONFLICT")
        # Prepare the operate values for the next contract boundary.
        data = request["content"].encode("utf-8")
        # Enforce SIZE_LIMIT boundaries.
        require(len(data) <= MAX_FILE, "SIZE_LIMIT")
        # Prepare the operate values for the next contract boundary.
        provenance = request["provenance"]
        # Enforce EVIDENCE_REQUIRED boundaries.
        require(set(provenance) == {"sources", "applicability", "status"}, "EVIDENCE_REQUIRED")
        evidence(provenance["sources"])
        token(provenance["applicability"])
        token(provenance["status"])
        # Stage the verified lifecycle changes in the issue state.
        state["provenance"][path] = {**provenance, "author": key, "supersedes": state["revision"]}
        files[path] = data
    # Apply the checkpoint transition.
    elif operation == "checkpoint":
        # Prepare the operate values for the next contract boundary.
        checkpoint = request["checkpoint"]
        # Enforce EVIDENCE_REQUIRED, SIZE_LIMIT boundaries.
        require(set(checkpoint) == {"goal", "constraints", "sources", "progress", "blockers", "handoff", "candidate"},
                "EVIDENCE_REQUIRED")
        require(len(canonical(checkpoint)) <= 16384, "SIZE_LIMIT")
        evidence(checkpoint["sources"])
        # Stage the verified lifecycle changes in the issue state.
        state.setdefault("checkpoint_history", []).append(checkpoint)
        state["checkpoint"] = checkpoint
        # Handle the case request.get('submitted_pr').
        if request.get("submitted_pr"):
            # Enforce EVIDENCE_REQUIRED boundaries.
            require(str(request["submitted_pr"]).startswith("https://"), "EVIDENCE_REQUIRED")
            # Stage the verified lifecycle changes in the issue state.
            state["disposition"] = "in_review"
            state["submitted_pr"] = request["submitted_pr"]
    # Require outcome evidence without bypassing pending work or completion obligations.
    elif operation == "outcome":
        # Prepare the operate values for the next contract boundary.
        disposition = request["disposition"]
        # Enforce the explicit identity and input contract.
        require(disposition in TERMINAL | {"active", "blocked", "in_review"})
        evidence(request.get("evidence"))
        # Prevent terminal state from stranding recorded pending operations.
        if disposition in TERMINAL:
            require(not any(p["pending"] for p in state["participants"].values()), "PENDING_OPERATION")
        # Handle the case disposition == 'completed'.
        if disposition == "completed":
            # Enforce EVIDENCE_REQUIRED boundaries.
            require(set(request.get("completion", {})) == {"human_acceptance", "merge", "obligations"}, "EVIDENCE_REQUIRED")
            # Process each refs under the same validation boundary.
            for refs in request["completion"].values():
                # Enforce the explicit identity and input contract.
                evidence(refs)
                # Process each ref under the same validation boundary.
                for ref in refs:
                    # Prepare the operate values for the next contract boundary.
                    path = Path(ref["locator"])
                    # Enforce EVIDENCE_REQUIRED boundaries.
                    require(path.is_absolute(), "EVIDENCE_REQUIRED")
                    # Keep filesystem resources scoped to this read or publication phase.
                    with Directory.absolute(path.parent) as source:
                        require(sha(source.read(path.name, MAX_FILE)) == ref["sha256"], "SOURCE_STALE")
            # Prepare the operate values for the next contract boundary.
            provider = request.get("provider_status", {})
            # Enforce EVIDENCE_REQUIRED boundaries.
            require(set(provider) == {"origin", "issue_uuid", "status_type", "completed_at", "request_id"}, "EVIDENCE_REQUIRED")
            require(provider["origin"] == "linear_get_issue" and provider["issue_uuid"] == state["issue_uuid"]
                    and provider["status_type"] == "completed" and provider["completed_at"], "EVIDENCE_REQUIRED")
            token(provider["request_id"])
            # Stage the verified lifecycle changes in the issue state.
            state["provider_completion"] = provider
        # Handle the case disposition == 'failed'.
        if disposition == "failed":
            require(request.get("abandoned") is True, "EVIDENCE_REQUIRED")
        # Stage the verified lifecycle changes in the issue state.
        state["disposition"] = disposition
        state["outcome"] = {"disposition": disposition, "evidence": request["evidence"],
                            "completion": request.get("completion"), "at": now()}
        state.setdefault("outcome_history", []).append(state["outcome"])
    # Apply the event transition.
    elif operation == "event":
        # Enforce INVALID_REQUEST boundaries.
        require(request.get("event_type") in {"check", "tool-start", "tool-complete", "observation"})
        require(set(request.get("event", {})) <= {"code"}, "INVALID_REQUEST")
        require(request.get("event", {}).get("code", "OK") in {"OK", "FAILED", "UNKNOWN", "INTERRUPTED"})
        # Prepare sequence and event values without dropping prior history.
        event_type = request["event_type"]
    # Apply the tool-start transition.
    elif operation == "tool-start":
        # Prepare the explicit source references for scoped validation.
        packet_reads(state, participant, files)
        # Enforce NOT_READY, REQUEST_CONFLICT boundaries.
        require(participant["ack"] == sha(canonical(participant["packet"])) and participant["status"] == "ready", "NOT_READY")
        tool = token(request["tool_id"])
        require(tool not in participant["pending"], "REQUEST_CONFLICT")
        # Stage the participant's pending-operation state for the next transaction.
        participant["pending"][tool] = {"status": "pending"}
    # Validate observation identity before settling or retaining asynchronous work.
    elif operation == "tool-complete":
        # Validate the observed tool-call identifier before accessing pending state.
        tool = token(request["tool_id"])
        handle = token(request["async_handle"]) if request.get("async_handle") is not None else None
        # Resolve exactly one original operation in this participant's pending set.
        if request.get("poll"):
            # Enforce UNKNOWN_OPERATION boundaries.
            require(handle is not None, "UNKNOWN_OPERATION")
            # Find all same-participant operations carrying the observed poll handle.
            matches = [identifier for identifier, pending in participant["pending"].items()
                       if pending.get("handle") == handle]
            # Enforce UNKNOWN_OPERATION boundaries.
            require(len(matches) == 1, "UNKNOWN_OPERATION")
            # Prepare the operate values for the next contract boundary.
            tool = matches[0]
        # Enforce UNKNOWN_OPERATION boundaries.
        require(tool in participant["pending"], "UNKNOWN_OPERATION")
        # Inspect the uniquely identified operation before changing its status.
        pending = participant["pending"][tool]
        # Preserve asynchronous handle identity across observations.
        if handle is not None and pending.get("handle") is not None:
            require(pending["handle"] == handle, "REQUEST_CONFLICT")
        # Settle explicit completion only after the handle checks above.
        if request.get("completed") is True:
            del participant["pending"][tool]
        # Retain asynchronous work when a valid handle is observed without completion.
        elif handle is not None:
            # Make repeated identical async transitions consume no additional event capacity.
            if pending["status"] == "unknown":
                event_type = None  # Duplicate transition cannot consume completion reserve.
            # Stage the participant's pending-operation state for the next transaction.
            participant["pending"][tool] = {"status": "unknown", "handle": handle}
        else:
            # Stop with the original failure rather than continue with incomplete state.
            raise WorkspaceError("UNKNOWN_OPERATION")
    # Enforce the shared lifecycle policy for this operation group.
    elif operation in {"detach", "reconcile-participant"}:
        # Stage the verified lifecycle changes in the issue state.
        target = key if operation == "detach" else request["target_participant"]
        member = state["participants"][target]
        # Enforce STALE_BINDING, PENDING_OPERATION boundaries.
        require(request.get("target_generation", request.get("binding_generation")) == member["generation"], "STALE_BINDING")
        evidence(request.get("evidence"))
        require(not member["pending"], "PENDING_OPERATION")
        # Prepare the operate values for the next contract boundary.
        member["status"], member["ack"] = "detached", None
    # Apply the transfer-coordinator transition.
    elif operation == "transfer-coordinator":
        # Prepare the operate values for the next contract boundary.
        target = request["target_participant"]
        # Enforce PENDING_OPERATION boundaries.
        require(target in state["participants"] and not participant["pending"], "PENDING_OPERATION")
        evidence(request.get("evidence"))
        # Stage the verified lifecycle changes in the issue state.
        state["coordinator"] = target
        state["owners"]["roadmap.md"] = target
    # Apply the reopen transition.
    elif operation == "reopen":
        # Enforce INVALID_REQUEST boundaries.
        evidence(request.get("evidence"))
        require(state["disposition"] in TERMINAL, "INVALID_REQUEST")
        # Stage the verified lifecycle changes in the issue state.
        state["disposition"] = "active"
        participant.update(status="attached", ack=None)
    # Rotate only bytes covered by the current verified provider checkpoint.
    elif operation == "event-rollover":
        # Enforce PENDING_OPERATION boundaries.
        require(not any(p["pending"] for p in state["participants"].values()), "PENDING_OPERATION")
        # Prepare the operate values for the next contract boundary.
        receipt, export = state.get("archive"), state.get("export")
        # Enforce ARCHIVE_PENDING boundaries.
        require(receipt and export and receipt["snapshot"] == export["snapshot"] and
                export["files"] == manifest(files) and export["revision"] == state["revision"], "ARCHIVE_PENDING")
        # Prepare sequence and event values without dropping prior history.
        data = files["events.jsonl"]
        # Enforce ARCHIVE_PENDING boundaries.
        require(data, "ARCHIVE_PENDING")
        # Stage the verified lifecycle changes in the issue state.
        first = strict_json(data.splitlines()[0])["seq"]
        name = f"events-{first:012d}-{state['seq']:012d}.jsonl"
        # Enforce INTEGRITY_ERROR boundaries.
        require(SEGMENT.fullmatch(name) and name not in files, "INTEGRITY_ERROR")
        # Retain exact checkpointed bytes. The same transaction publishes the segment,
        # resets the active stream and appends the next globally chained event.
        files[name], files["events.jsonl"] = data, b""
        state.setdefault("event_segments", []).append({
            "path": name, "first": first, "last": state["seq"], "head": state["head"],
            "sha256": sha(data), "archive": receipt})
        result = {"segment": name, "snapshot": receipt["snapshot"]}
    # Commit preparation before freezing the immutable reconstructable snapshot.
    elif operation == "archive-prepare":
        # Enforce PENDING_OPERATION boundaries.
        require(not any(p["pending"] for p in state["participants"].values()), "PENDING_OPERATION")
        # Handle the case request.get('seal').
        if request.get("seal"):
            # Enforce the explicit identity and input contract.
            require(state["disposition"] in TERMINAL and all(
                k == key or p["status"] == "detached" for k, p in state["participants"].items()), "RETAINED")
            # Prepare the operate values for the next contract boundary.
            participant["status"] = "maintenance"
        # Commit seal/preparation event BEFORE the frozen snapshot.
        prepare_request = {**request, "request_id": request["request_id"] + ":prepare"}
        prior = state["requests"].get(sha(prepare_request["request_id"].encode()))
        # Handle the case prior.
        if prior:
            require(prior["digest"] == sha(canonical(prepare_request)), "REQUEST_CONFLICT")
        else:
            # Publish this lifecycle result through the recoverable transaction.
            issue.commit(state, files, prepare_request, {}, "archive-prepare")
        # Stage the verified lifecycle changes in the issue state.
        state = copy.deepcopy(issue.state)
        files = issue.files()
        snapshot = archive_payload(state, files)
        export = export_documents(snapshot)
        state["export"] = {"snapshot": export["snapshot"], "revision": state["revision"],
                           "files": manifest(files), "request_id": request["request_id"]}
        # Enforce ARCHIVE_PENDING boundaries.
        require(len(canonical(export)) <= MAX_REQUEST, "ARCHIVE_PENDING")
        # Publish validated content and flush the required filesystem boundary.
        issue.control.put("export-" + export["snapshot"] + ".json", export)
        return issue.commit(state, files, request, export)
    # Build the index only from matching independent part identities and versions.
    elif operation == "archive-index":
        # Read operate inputs through the scoped file interface.
        export = issue.control.json("export-" + state["export"]["snapshot"] + ".json")
        # Prepare the operate values for the next contract boundary.
        observations = request["observations"]
        # Enforce ARCHIVE_PENDING boundaries.
        require(len(observations) == len(export["parts"]), "ARCHIVE_PENDING")
        # Prepare the operate values for the next contract boundary.
        descriptors = []
        # Process each (observed, wanted) under the same validation boundary.
        for observed, wanted in zip(observations, export["parts"]):
            # Stage the verified lifecycle changes in the issue state.
            part = provider_observation(observed, state["issue_uuid"])
            # Enforce ARCHIVE_PENDING boundaries.
            require(part == parse_document(wanted["content"]), "ARCHIVE_PENDING")
            # Prepare the operate values for the next contract boundary.
            descriptors.append({"id": observed["id"], "updatedAt": observed["updatedAt"], "sha256": sha(canonical(part))})
        # Enforce ARCHIVE_PENDING boundaries.
        require(len({d["id"] for d in descriptors}) == len(descriptors), "ARCHIVE_PENDING")
        # Stage the verified lifecycle changes in the issue state.
        root = {"schema_version": 1, "kind": "index", "snapshot": export["snapshot"], "parts": descriptors}
        content = document(root, f"# {issue.id} verified archive index\n\nDisposition: {state['disposition']}.\n"
                           "Reconstructable roadmap, context and event history are in the numbered parts.\n"
                           "This archive is evidence, not human acceptance or execution authority.")
        result = {"issue": state["issue_uuid"], "title": f"{issue.id} snapshot {export['snapshot']} index", "content": content}
        # Enforce ARCHIVE_PENDING boundaries.
        require(len(content.encode()) <= DOCUMENT_LIMIT, "ARCHIVE_PENDING")
        # Stage the verified lifecycle changes in the issue state.
        state["index_request"] = result.copy()
        event_type = None
    # Enforce the shared lifecycle policy for this operation group.
    elif operation in {"archive-save-start", "archive-observe-save"}:
        # Read operate inputs through the scoped file interface.
        export = issue.control.json("export-" + state["export"]["snapshot"] + ".json")
        # Stage the verified lifecycle changes in the issue state.
        documents = export["parts"] + ([state["index_request"]] if state.get("index_request") else [])
        digest = request["content_digest"]
        # Enforce ARCHIVE_PENDING boundaries.
        require(digest in {sha(doc["content"].encode()) for doc in documents}, "ARCHIVE_PENDING")
        # Prepare the operate values for the next contract boundary.
        saves = state.setdefault("provider_saves", {})
        # Apply the archive-save-start transition.
        if operation == "archive-save-start":
            # Enforce ARCHIVE_PENDING boundaries.
            require(digest not in saves, "ARCHIVE_PENDING", "Uncertain save: locate and read the matching document before another save.")
            # Prepare the operate values for the next contract boundary.
            saves[digest] = {"status": "uncertain"}
        else:
            # Enforce the explicit identity and input contract.
            document_id = token(request["document_id"])
            # Prepare the operate values for the next contract boundary.
            existing = saves.get(digest, {})
            # Enforce ARCHIVE_PENDING boundaries.
            require(not existing.get("id") or existing["id"] == document_id, "ARCHIVE_PENDING")
            # Prepare the operate values for the next contract boundary.
            saves[digest] = {"status": "readback-required", "id": document_id}
        # Prepare sequence and event values without dropping prior history.
        event_type = None
    # Enforce the shared lifecycle policy for this operation group.
    elif operation in {"archive-verify", "cleanup-commit"}:
        # Prepare the operate values for the next contract boundary.
        snapshot, archived, receipt = verify_provider(state, request.get("observations"))
        # Enforce ARCHIVE_PENDING boundaries.
        require(manifest(archived) == manifest(files) == state["export"]["files"] and
                snapshot["revision"] == state["revision"] == state["export"]["revision"], "ARCHIVE_PENDING")
        # Apply the archive-verify transition.
        if operation == "archive-verify":
            # Stage the verified lifecycle changes in the issue state.
            state["archive"] = receipt
            result = {"receipt": receipt}
            event_type = None
        else:
            # Prepare the operate values for the next contract boundary.
            eligible(state)
            # Enforce ARCHIVE_PENDING boundaries.
            require(receipt["root"]["id"] == state["archive"]["root"]["id"] and
                    receipt["root"]["updatedAt"] == state["archive"]["root"]["updatedAt"], "ARCHIVE_PENDING")
            # Prepare the operate values for the next contract boundary.
            challenge = request.get("cleanup_challenge")
            # Enforce ARCHIVE_PENDING boundaries.
            require(challenge and challenge == state.get("cleanup_challenge") and
                    all(o["request_id"] == challenge for o in request["observations"]), "ARCHIVE_PENDING")
            # Stage the verified lifecycle changes in the issue state.
            state["archive"] = receipt
            result = {"ok": True, "code": "CLEANED", "revision": state["revision"], "issue_id": issue.id}
            state["requests"][sha(request["request_id"].encode())] = {"digest": sha(canonical(request)), "result": result}
            intent = {"state": state, "files": state["files"], "directory_identity": state["directory_identity"],
                      "quarantine": "quarantine-" + uuid.uuid4().hex, "request_id": request["request_id"]}
            # Publish validated content and flush the required filesystem boundary.
            issue.control.put("cleanup.json", intent)
            # Prepare the operate values for the next contract boundary.
            fault("cleanup-intent")
            finish_cleanup(issue)
            return result
    # Apply the cleanup-plan transition.
    elif operation == "cleanup-plan":
        # Stage the verified lifecycle changes in the issue state.
        eligible(state)
        state["cleanup_challenge"] = str(uuid.uuid4())
        result = {"code": "READBACK_REQUIRED", "cleanup_challenge": state["cleanup_challenge"],
                  "root": state["archive"]["root"], "parts": state["archive"]["parts"]}
        event_type = None
    else:
        # Stop with the original failure rather than continue with incomplete state.
        raise WorkspaceError("UNSUPPORTED_OPERATION")
    return issue.commit(state, files, request, result, event_type)



def rebind(store, request):
    """Move one explicit session binding while fencing and preserving its old issue.

    Args:
        store: Validated shared repository Store.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The target participant binding result.

    Raises:
        WorkspaceError: If target assignment, generations, pending work or retry identity conflicts.
        OSError: If either issue or binding cannot be persisted.
    """
    # Enforce BINDING_CONFLICT boundaries.
    old_id, new_id = issue_id(request["issue_id"]), issue_id(request["new_issue_id"])
    require(old_id != new_id, "BINDING_CONFLICT")
    # Prepare the rebind values for the next contract boundary.
    key = participant_key(request)
    # Enforce the explicit identity and input contract.
    evidence(request.get("evidence"))
    # Keep filesystem resources scoped to this read or publication phase.
    with ExitStack() as stack:
        # Prepare the rebind values for the next contract boundary.
        controls = {}
        # Process each identifier under the same validation boundary.
        for identifier in sorted([old_id, new_id]):
            # Prepare the rebind values for the next contract boundary.
            control = stack.enter_context(store.issues.child(identifier))
            stack.enter_context(control.lock())
            controls[identifier] = control
        # Prepare the rebind values for the next contract boundary.
        old, new = Issue(store, controls[old_id], old_id), Issue(store, controls[new_id], new_id)
        old.recover()
        new.recover()
        # Enforce RECOVERY_REQUIRED boundaries.
        require(old.state and new.state, "RECOVERY_REQUIRED")
        # Stage the verified lifecycle changes in the issue state.
        old_files, new_files = old.files(), new.files()
        old_state, new_state_value = copy.deepcopy(old.state), copy.deepcopy(new.state)
        member = old_state["participants"].get(key)
        # Enforce STALE_BINDING, PENDING_OPERATION boundaries.
        require(member and member["generation"] == request["binding_generation"], "STALE_BINDING")
        require(not member["pending"], "PENDING_OPERATION")
        require(key != old_state["coordinator"] or all(k == key or p["status"] == "detached"
                for k, p in old_state["participants"].items()), "PENDING_OPERATION")
        # Build the request from explicit caller or observed session identities.
        target_request = {**request, "operation": "rebind", "issue_id": new_id, "old_issue_id": old_id,
                          "binding_generation": request.get("new_binding_generation")}
        previous = new_state_value["requests"].get(sha(request["request_id"].encode()))
        # Handle the case previous.
        if previous:
            # Enforce REQUEST_CONFLICT boundaries.
            require(previous["digest"] == sha(canonical(target_request)), "REQUEST_CONFLICT")
            # Prepare the rebind values for the next contract boundary.
            store.view(new_id)
            # Persist the committed participant generation for this worktree.
            store.save_binding(new.state, key)
            return previous["result"]
        # Prepare the rebind values for the next contract boundary.
        result = attach(new_state_value, target_request)
        member.update(status="detached", ack=None)
        # Handle the case member != old.state['participants'][key].
        if member != old.state["participants"][key]:
            old.commit(old_state, old_files, {**request, "request_id": request["request_id"] + ":retire"}, {}, "rebind")
        # Publish this lifecycle result through the recoverable transaction.
        result = new.commit(new_state_value, new_files, target_request, result, "rebind")
        # Prepare the rebind values for the next contract boundary.
        store.view(new_id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(new.state, key)
        # Prepare the rebind values for the next contract boundary.
        assignment = key + ".assignment.json"
        # Validate any existing entry before reusing or replacing it.
        if store.bindings.exists(assignment):
            store.bindings.put(assignment, {"issue_id": new_id})
        return result


def collect_candidates(store, request):
    """Attempt cleanup only for explicitly supplied separately bound maintenance sessions.

    Args:
        store: Validated shared repository Store.
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        Per-issue cleanup results, including retained or failed candidates.

    Raises:
        WorkspaceError: If the candidate list or session separation is invalid.
    """
    # Prepare the collect_candidates values for the next contract boundary.
    candidates = request["cleanup_candidates"]
    # Enforce the explicit identity and input contract.
    require(isinstance(candidates, list) and len(candidates) <= 16)
    # Prepare the collect_candidates values for the next contract boundary.
    results = []
    # Process each candidate under the same validation boundary.
    for candidate in candidates:
        # Enforce INVALID_REQUEST boundaries.
        identifier = issue_id(candidate["issue_id"])
        require(identifier != request["issue_id"], "INVALID_REQUEST")
        # A separate bound maintenance session avoids retiring the new task's owner.
        cleanup = {**candidate, "schema_version": 1, "operation": "cleanup-commit",
                   "request_id": request["request_id"] + ":collect:" + identifier,
                   "worktree": request["worktree"], "repo_id": request["repo_id"]}
        # Enforce BINDING_CONFLICT boundaries.
        require(cleanup.get("session_id") != request["session_id"], "BINDING_CONFLICT")
        # Invoke the core boundary and retain its structured result.
        results.append({"issue_id": identifier, **execute(cleanup)})
    return results



def permission_paths(request):
    """Derive narrow issue/control paths for a failed filesystem permission request.

    Args:
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The paths relevant to registration or the requested issue.
    """
    # Prepare the permission_paths values for the next contract boundary.
    root = Path(request.get("worktree", "/"))
    # Attempt this phase while retaining its error and cleanup paths.
    try:
        root = repository(root)[0]
    except (OSError, WorkspaceError, subprocess.SubprocessError):
        # Preserve or translate this failure according to the enclosing contract.
        pass
    # Prepare the permission_paths values for the next contract boundary.
    main = request.get("main_worktree")
    # Handle the case main is None.
    if main is None:
        # Attempt this phase while retaining its error and cleanup paths.
        try:
            # Keep filesystem resources scoped to this read or publication phase.
            with Directory.absolute(root) as directory, directory.child(".task") as local:
                main = local.json(".repository.json")["main"]
        except (OSError, WorkspaceError, KeyError):
            # Preserve or translate this failure according to the enclosing contract.
            pass
    # Prepare the permission_paths values for the next contract boundary.
    paths = [str(root / ".task/.repository.json"), str(root / ".task/.bindings")]
    # Handle the case main.
    if main:
        # Prepare the permission_paths values for the next contract boundary.
        store = Path(main) / ".task"
        paths.append(str(store / ".control/repository.json"))
        identifier = request.get("issue_id")
        # Check the supported input shape before interpreting its fields.
        if isinstance(identifier, str) and ISSUE.fullmatch(identifier):
            paths.extend([str(store / identifier), str(store / ".control/issues" / identifier)])
        # Handle the case request.get('operation') == 'register'.
        elif request.get("operation") == "register":
            paths.extend([str(store), str(store / ".control")])
    return paths


def execute(request):
    """Execute a bounded request and return safe diagnostics without exception contents.

    Args:
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        A success result or a structured failure with a public code and recovery action.
    """
    # Attempt this phase while retaining its error and cleanup paths.
    try:
        # Enforce the explicit identity and input contract.
        require(isinstance(request, dict) and len(canonical(request)) <= MAX_REQUEST)
        require(request.get("schema_version") == 1)
        token(request["request_id"])
        # Prepare the execute values for the next contract boundary.
        operation = request["operation"]
        # Apply the register transition.
        if operation == "register":
            # Enforce INVALID_REQUEST boundaries.
            require("repo_id" not in request, "INVALID_REQUEST")
            return register(request)
        # Apply the diagnose' and 'repo_id' not in request transition.
        if operation == "diagnose" and "repo_id" not in request:
            # Prepare the execute values for the next contract boundary.
            root, _, _ = repository(request["worktree"])
            # Keep filesystem resources scoped to this read or publication phase.
            with Directory.absolute(root) as directory:
                # Validate any existing entry before reusing or replacing it.
                if not directory.exists(".task"):
                    return {"ok": True, "code": "REGISTRATION_REQUIRED"}
                # Keep filesystem resources scoped to this read or publication phase.
                with directory.child(".task") as local:
                    # Validate any existing entry before reusing or replacing it.
                    if not local.exists(".repository.json"):
                        return {"ok": True, "code": "REGISTRATION_REQUIRED"}
                    # Read repository registration through validated directory handles.
                    reg = local.json(".repository.json")
                    return {"ok": True, "code": "REGISTERED", "repo_id": reg["repo_id"]}
        # Enforce the explicit identity and input contract.
        identifier = issue_id(request["issue_id"])
        token(request["repo_id"])
        # Prepare the execute values for the next contract boundary.
        participant_key(request)
        # Hold the required locks while validating or publishing shared state.
        with Store(request) as store, store.bindings.lock(participant_key(request) + ".lock"):
            # Apply the rebind transition.
            if operation == "rebind":
                return rebind(store, request)
            # Hold the required locks while validating or publishing shared state.
            with store.issues.child(identifier, True) as control, control.lock():
                result = operate(store, Issue(store, control, identifier), request)
            # Separate successful lifecycle results from recovery diagnostics.
            if result["ok"] and operation == "create" and request.get("cleanup_candidates"):
                result["collection"] = collect_candidates(store, request)
            return result
    except WorkspaceError as error:
        # Preserve or translate this failure according to the enclosing contract.
        return {"ok": False, "code": error.code, "action": error.action}
    except PermissionError as error:
        # Preserve or translate this failure according to the enclosing contract.
        paths = permission_paths(request)
        return {"ok": False, "code": "PERMISSION_REQUIRED", "paths": paths,
                "operation": request.get("operation"),
                "action": "Grant only the listed issue/control and binding paths, then retry this operation."}
    except (OSError, subprocess.SubprocessError):
        # Preserve or translate this failure according to the enclosing contract.
        return {"ok": False, "code": "RECOVERY_REQUIRED", "action": "Inspect filesystem identity and unfinished transactions; retain all data."}
    except (KeyError, TypeError, ValueError, UnicodeError, AttributeError):
        # Preserve or translate this failure according to the enclosing contract.
        return {"ok": False, "code": "INVALID_REQUEST", "action": "Use the documented version 1 request fields."}


def main():
    """Read one bounded CLI request and emit one canonical result.

    Returns:
        Zero for success or three for a reported request failure.
    """
    # Configure the single-request CLI without adding alternate bootstrap commands.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-json", help="One bounded JSON object; bootstrap-safe argument route")
    args = parser.parse_args()
    # Attempt this phase while retaining its error and cleanup paths.
    try:
        # Read main inputs through the scoped file interface.
        raw = args.request_json.encode() if args.request_json is not None else sys.stdin.buffer.read(MAX_REQUEST + 1)
        # Enforce SIZE_LIMIT boundaries.
        require(len(raw) <= MAX_REQUEST, "SIZE_LIMIT")
        # Invoke the core boundary and retain its structured result.
        result = execute(strict_json(raw))
    except WorkspaceError as error:
        # Preserve or translate this failure according to the enclosing contract.
        result = {"ok": False, "code": error.code}
    # Prepare the main values for the next contract boundary.
    print(canonical(result).decode())
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
