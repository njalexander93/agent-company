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
    require(isinstance(value, str) and len(value) <= (limit + 2) // 3 * 4, "SIZE_LIMIT")
    try:
        # Reject invalid base64 before checking the decoded byte budget.
        result = base64.b64decode(value, validate=True)
    except ValueError:
        raise WorkspaceError("INTEGRITY_ERROR") from None
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
        result = {}
        # Reject duplicate keys instead of silently accepting the last value.
        for key, value in items:
            require(key not in result, "INVALID_REQUEST")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(WorkspaceError("INVALID_REQUEST")))
    except (ValueError, UnicodeError):
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
        self.fd = fd
        s = os.fstat(fd)
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == os.getuid(), "UNSAFE_PATH")
        require(not private or not s.st_mode & 0o022, "UNSAFE_PATH")
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
        # Reject relative traversal before opening the absolute directory chain.
        path = Path(path)
        require(path.is_absolute() and ".." not in path.parts, "UNSAFE_PATH")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            # Open each component without following links; retain only the current descriptor.
            for name in path.parts[1:]:
                nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            return cls(fd)
        except BaseException:
            os.close(fd)
            raise

    def close(self):
        """Release this directory descriptor.

        Raises:
            OSError: If the descriptor cannot be closed.
        """
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
        try:
            os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
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
        require("/" not in name and name not in {"", ".", ".."}, "UNSAFE_PATH")
        # Create a private child if absent; validate existing children through the same open below.
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=self.fd)
                os.fsync(self.fd)
            except FileExistsError:
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
        # Open without following links or blocking on a substituted special file.
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            s = os.fstat(fd)
            require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and
                    s.st_uid == os.getuid() and not s.st_mode & 0o022, "UNSAFE_PATH")
            require(s.st_size <= limit, "SIZE_LIMIT")
            # Read one extra byte to catch growth beyond the size checked above.
            with os.fdopen(fd, "rb", closefd=False) as stream:
                result = stream.read(limit + 1)
            require(len(result) <= limit, "SIZE_LIMIT")
            return result
        finally:
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
        require("/" not in name and name not in {"", ".", ".."}, "UNSAFE_PATH")
        # Validate any existing entry before reusing or replacing it.
        if self.exists(name):
            self.read(name)  # Refuse links/special files before replacement.
        # Stage bytes in an exclusively created private file before replacing the destination.
        temp = ".write-" + uuid.uuid4().hex
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
            # Publish only flushed bytes, then make the directory update durable.
            os.replace(temp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            os.close(fd)
            # Remove any unpublished temporary file after closing its descriptor.
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
        self.write(name, canonical(value))

    def unlink(self, name):
        """Remove a direct entry and flush the containing directory.

        Args:
            name: Direct entry name relative to the opened directory.

        Raises:
            OSError: If removal or directory flushing fails.
        """
        os.unlink(name, dir_fd=self.fd)
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
            try:
                fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.fd)
                os.fsync(self.fd)
                break
            except FileExistsError:
                try:
                    fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
                    break
                except FileNotFoundError:
                    require(time.monotonic() < end, "BUSY")
        try:
            s = os.fstat(fd)
            require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and
                    s.st_uid == os.getuid() and not s.st_mode & 0o022, "UNSAFE_PATH")
            # Set the monotonic deadline for bounded lock acquisition.
            end = time.monotonic() + timeout
            # Retry until success or the bounded deadline is reached.
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    require(time.monotonic() < end, "BUSY", "Retry after the current writer finishes.")
                    time.sleep(0.01)
            yield
        finally:
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
    # Query Git with separate arguments and a bounded runtime.
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                            text=True, timeout=2, check=False)
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
    # Resolve the worktree and common Git directory before checking membership.
    root = Path(git(path, "rev-parse", "--show-toplevel"))
    require(git(root, "rev-parse", "--is-bare-repository") == "false", "HOST_UNSUPPORTED")
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    trees = [Path(line[9:]) for line in git(root, "worktree", "list", "--porcelain").splitlines()
             if line.startswith("worktree ")]
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
    root, common, trees = repository(request["worktree"])
    main = Path(request["main_worktree"])
    require(main == trees[0] and main in trees, "REPOSITORY_MISMATCH")
    require(repository(main)[1] == common, "REPOSITORY_MISMATCH")
    # Bind registration to both paths and filesystem identities.
    identity = {"schema_version": 1, "common": str(common), "main": str(main),
                "common_identity": fs_identity(common), "main_identity": fs_identity(main)}
    with Directory.absolute(main) as directory, directory.child(".task", True) as task, \
            task.child(".control", True) as control, control.lock("registration.lock"):
        # Reuse registration only when every canonical identity still matches.
        if control.exists("repository.json"):
            existing = control.json("repository.json")
            require(all(existing[k] == v for k, v in identity.items()), "REPOSITORY_MISMATCH")
            identity = existing
        else:
            identity["repo_id"] = str(uuid.uuid4())
            control.put("repository.json", identity)
        with Directory.absolute(root) as worktree, worktree.child(".task", True) as local:
            # Validate any existing entry before reusing or replacing it.
            if local.exists(".repository.json"):
                require(local.json(".repository.json") == identity, "REPOSITORY_MISMATCH")
            local.put(".repository.json", identity)
            # Persist only an explicit coordinator-owned startup packet for this session.
            if request.get("startup") is not None:
                setup = request["startup"]
                require(isinstance(setup, dict) and set(setup) == {"issue_id", "issue_uuid", "packet", "coordinator"})
                issue_id(setup["issue_id"])
                token(setup["issue_uuid"])
                key = participant_key(request)
                require(setup["coordinator"] == key, "NOT_OWNER")
                validate_packet(setup["packet"])
                require(all(ref["reader"] == key for ref in setup["packet"]), "SCOPE_MISSING")
                with local.child(".bindings", True) as bindings:
                    name = key + ".startup.json"
                    # Validate any existing entry before reusing or replacing it.
                    if bindings.exists(name):
                        require(bindings.json(name) == setup, "BINDING_CONFLICT")
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
        # Track opened handles so partial store initialization can release them.
        self.request = request
        self.root, common, trees = repository(request["worktree"])
        self.handles = []
        try:
            self.local_root = self.keep(Directory.absolute(self.root))
            self.local = self.keep(self.local_root.child(".task"))
            # Read repository registration through validated directory handles.
            self.registration = self.local.json(".repository.json")
            reg = self.registration
            require(request.get("repo_id") == reg["repo_id"], "REPOSITORY_MISMATCH")
            require(str(common) == reg["common"] and fs_identity(common) == reg["common_identity"]
                    and Path(reg["main"]) == trees[0], "REPOSITORY_MISMATCH")
            self.main = self.keep(Directory.absolute(reg["main"]))
            require(self.main.identity == reg["main_identity"], "REPOSITORY_MISMATCH")
            self.task = self.keep(self.main.child(".task"))
            self.control = self.keep(self.task.child(".control"))
            require(self.control.json("repository.json") == reg, "REPOSITORY_MISMATCH")
            self.issues = self.keep(self.control.child("issues", True))
            self.bindings = self.keep(self.local.child(".bindings", True))
        except BaseException:
            self.close()
            raise

    def keep(self, directory):
        """Track a descriptor for reverse-order store cleanup.

        Args:
            directory: Opened Directory handle for the relevant payload or store.

        Returns:
            The supplied directory handle.
        """
        self.handles.append(directory)
        return directory

    def close(self):
        """Release all tracked descriptors in reverse opening order.

        Raises:
            OSError: If a tracked descriptor cannot be closed.
        """
        # Close child descriptors before their parents, including partial initialization.
        for directory in reversed(self.handles):
            directory.close()
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
        self.close()

    def binding(self):
        """Read this host/session binding without inventing an assignment.

        Returns:
            The binding object, or None when it does not exist.

        Raises:
            WorkspaceError: If stored binding data is unsafe or malformed.
            OSError: If binding access fails.
        """
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
        # The main worktree already exposes the canonical payload.
        if str(self.root) == self.registration["main"]:
            return
        target = str(Path(self.registration["main"]) / ".task" / issue)
        # Validate any existing entry before reusing or replacing it.
        if not self.local.exists(issue):
            try:
                os.symlink(target, issue, dir_fd=self.local.fd)
                os.fsync(self.local.fd)
            except FileExistsError:
                pass
        # Require the worktree view to be exactly the intended symlink, then validate its target.
        s = os.stat(issue, dir_fd=self.local.fd, follow_symlinks=False)
        require(stat.S_ISLNK(s.st_mode) and os.readlink(issue, dir_fd=self.local.fd) == target,
                "UNSAFE_PATH")
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
    result = {}
    # Inspect only direct issue entries under the opened directory descriptor.
    for name in os.listdir(directory.fd):
        # Validate narrow metadata entries before excluding them from task content.
        if os_metadata(name):
            directory.read(name, MAX_FILE)
            continue
        # Inspect the supported context directory without allowing arbitrary descendants.
        if name == "context":
            with directory.child(name, private=False) as context:
                # Validate each direct context entry without descending into arbitrary directories.
                for note in os.listdir(context.fd):
                    # Validate narrow metadata entries before excluding them from task content.
                    if os_metadata(note):
                        context.read(note, MAX_FILE)
                        continue
                    path = payload_path("context/" + note)
                    result[path] = context.read(note, MAX_FILE)
        else:
            payload_path(name, events=True)
            result[name] = directory.read(name, MAX_FILE)
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
    payload_path(path, events=True)
    # Route context writes through the opened context directory, not arbitrary traversal.
    if path.startswith("context/"):
        with directory.child("context", True, private=False) as context:
            context.write(path[8:], data)
    else:
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
    require(not data or data.endswith(b"\n"), "INTEGRITY_ERROR")
    # Validate every complete event against the expected sequence and prior digest.
    for line in data.splitlines():
        require(len(line) <= 8192, "INTEGRITY_ERROR")
        event = strict_json(line)
        digest = event.pop("digest", None)
        require(event["seq"] == seq + 1 and event["prev_digest"] == prior and
                sha(canonical(event)) == digest, "INTEGRITY_ERROR")
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
    seq, head = 0, None
    # Validate retained segment ranges before continuing into the active stream.
    for path in sorted(p for p in files if SEGMENT.fullmatch(p)):
        first, last = map(int, SEGMENT.fullmatch(path).groups())
        require(first == seq + 1 and last >= first, "INTEGRITY_ERROR")
        seq, head = validate_events(files[path], seq, head)
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
        self.store, self.control, self.id = store, control, identifier
        self.state = control.json("state.json") if control.exists("state.json") else None

    def files(self):
        """Read the committed payload and quarantine only a provable interrupted tail.

        Returns:
            Validated payload paths and exact bytes.

        Raises:
            WorkspaceError: If identity, manifest or event integrity fails.
            OSError: If payload access or tail quarantine fails.
        """
        require(self.state and self.state["storage"] != "cleaned", "RECOVERY_REQUIRED")
        with self.store.task.child(self.id, private=False) as payload:
            require(payload.identity == self.state["directory_identity"], "UNSAFE_PATH")
            result = inventory(payload)
        # Reconcile only an interrupted tail that reproduces the exact committed manifest.
        if manifest(result) != self.state["files"]:
            data = result.get("events.jsonl", b"")
            end = data.rfind(b"\n") + 1
            prefix, tail = data[:end], data[end:]
            corrected = {**result, "events.jsonl": prefix}
            # Quarantine only uncommitted tail bytes; never truncate an altered committed prefix.
            if tail and manifest(corrected) == self.state["files"]:
                self.control.write("interrupted-tail-" + sha(tail), tail)
                with self.store.task.child(self.id, private=False) as payload:
                    payload.write("events.jsonl", prefix)
                result = corrected
                self.control.put("diagnostic-loss.json", {"discarded_tail_bytes": len(tail), "at": now()})
            else:
                raise WorkspaceError("UNTRACKED_CHANGE")
        seq, head = validate_history(result)
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
        tx = self.control.json("transaction.json")
        state = tx["state"]
        # Before any publication, prove every file is either its old or staged version.
        if not self.store.task.exists(self.id):
            require(tx["base"] is None, "RECOVERY_REQUIRED")
            with self.store.task.child(self.id, True, private=False):
                pass
        with self.store.task.child(self.id, private=False) as payload:
            if tx["base"] is not None:
                require(payload.identity == tx["directory_identity"], "UNSAFE_PATH")
            present = inventory(payload, require_roadmap=False)
            require(set(present) <= set(state["files"]), "UNTRACKED_CHANGE")
            # Prove each present file matches either the old or intended transaction digest.
            for path, data in present.items():
                old = (tx["base"] or {}).get(path)
                require(sha(data) in {old, state["files"][path]}, "UNTRACKED_CHANGE")
            # Publish staged files through the same safe payload writer used by normal commits.
            for path, data in tx["writes"].items():
                write_payload(payload, path, decode(data))
                fault("payload:" + path)
            payload.child("context", True, private=False).close()
            require(manifest(inventory(payload)) == state["files"], "RECOVERY_REQUIRED")
            state["directory_identity"] = payload.identity
            os.fsync(payload.fd)
        # Publish state only after payload durability; remove the intent last so recovery can retry.
        fault("payload-flush")
        self.control.put("state.json", state)
        fault("state")
        self.control.unlink("transaction.json")
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
        if event_type:
            require(request.get("event") is None or request["operation"] == "event", "INVALID_REQUEST")
            optional = event_type == "observation"
            # Reserve async-transition and completion events for every admitted pending tool.
            pending_slots = sum(1 if tool["status"] == "unknown" else 2
                                for member in state["participants"].values()
                                for tool in member["pending"].values())
            reserved = 8192 * (1 + pending_slots)
            # Suppress optional observations before they consume settlement reserves.
            if optional and len(files.get("events.jsonl", b"")) + reserved + 8192 > MAX_EVENTS:
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
            require(len(line) <= 8192, "SIZE_LIMIT")
            files["events.jsonl"] = files.get("events.jsonl", b"") + line
            # Keep enough room to freeze a provider checkpoint at the boundary.
            limit = MAX_EVENTS if event_type == "archive-prepare" else MAX_EVENTS - reserved
            require(len(files["events.jsonl"]) <= limit, "ARCHIVE_PENDING")
            state["seq"], state["head"] = event["seq"], event["digest"]
            state.pop("archive", None)
        # Bind the next state and response to the exact intended payload inventory.
        state["files"] = manifest(files)
        result.update(ok=True, code=result.get("code", "OK"), revision=state["revision"],
                      repo_id=state["repo_id"], issue_id=self.id)
        stored_result = result
        # Store an immutable snapshot reference for archive-prepare retries.
        if "parts" in result and "snapshot" in result and request["operation"] == "archive-prepare":
            stored_result = {k: v for k, v in result.items() if k != "parts"}
            stored_result["result_ref"] = "export-" + result["snapshot"] + ".json"
        state["requests"][sha(request["request_id"].encode())] = {
            "digest": sha(canonical(request)), "result": stored_result}
        require(len(canonical(state)) <= 8 * 1024 * 1024, "ARCHIVE_PENDING")
        tx = {"base": self.state["files"] if self.state else None,
              "directory_identity": self.state.get("directory_identity") if self.state else None,
              "state": state, "writes": {p: encode(b) for p, b in files.items() if old_files.get(p) != b}}
        require(len(canonical(tx)) <= MAX_REQUEST, "SIZE_LIMIT")
        # Publish the durable recovery intent before mutating payload or state files.
        fault("before-intent")
        self.control.put("transaction.json", tx)
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
    key = participant_key(request)
    participant = state["participants"].get(key)
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
    require(isinstance(value, list) and 0 < len(value) <= 16, "EVIDENCE_REQUIRED")
    # Require attributable evidence with bounded locators and content digests.
    for item in value:
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
    require(isinstance(packet, list) and len(packet) <= 64, "SCOPE_MISSING")
    identifiers = set()
    # Require unique reference IDs and explicit reader, authority, and lifecycle scope.
    for ref in packet:
        require(isinstance(ref, dict) and set(ref) == {"id", "locator", "sha256", "required",
                "authority", "reason", "stage", "reader"}, "SCOPE_MISSING")
        token(ref["id"])
        require(ref["id"] not in identifiers and DIGEST.fullmatch(ref["sha256"]), "SCOPE_MISSING")
        identifiers.add(ref["id"])
        require(type(ref["required"]) is bool, "SCOPE_MISSING")
        for field in ("authority", "reason", "stage", "reader"):
            token(ref[field])
        locator = ref["locator"]
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
    require(participant.get("packet") is not None, "SCOPE_MISSING")
    refs = []
    # Read only assigned references and compare their exact content digests.
    for ref in participant["packet"]:
        data = None
        try:
            # Open external references safely; resolve relative references from validated payload bytes.
            if Path(ref["locator"]).is_absolute():
                path = Path(ref["locator"])
                with Directory.absolute(path.parent) as parent:
                    data = parent.read(path.name, MAX_FILE)
            else:
                data = files.get(ref["locator"])
        except (OSError, WorkspaceError):
            # Missing optional sources remain unavailable; required sources block readiness.
            if ref["required"]:
                raise WorkspaceError("SOURCE_STALE") from None
        valid = data is not None and sha(data) == ref["sha256"]
        require(valid or not ref["required"], "SOURCE_STALE")
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
    key = participant_key(request)
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
    key = participant_key(request)
    require(state["disposition"] not in TERMINAL and state["storage"] == "present", "RECOVERY_REQUIRED")
    participant = state["participants"].get(key)
    # Preserve existing scope and require the recorded generation before resuming.
    if participant:
        initial_create = (request["operation"] == "create" and request.get("binding_generation") is None
                          and participant["generation"] == 1 and key == state["coordinator"])
        require(initial_create or request.get("binding_generation") == participant["generation"], "STALE_BINDING")
        require(participant["status"] in {"attached", "ready", "detached"}, "STALE_BINDING")
        # Fence the old binding and require a new acknowledgment after resumption.
        if participant["status"] == "detached":
            participant["generation"] += 1
            participant["status"] = "attached"
            participant["ack"] = None
    else:
        require(key == state["coordinator"] or key in state.get("assignments", {}), "SCOPE_MISSING")
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
    result = {"schema_version": 1, "repo_id": state["repo_id"], "issue_id": state["issue_id"],
            "issue_uuid": state["issue_uuid"], "revision": state["revision"],
            "disposition": state["disposition"], "outcome": state.get("outcome"),
            "checkpoint": state.get("checkpoint"), "provenance": state.get("provenance", {}),
            "adoption": state.get("adoption"), "checkpoint_history": state.get("checkpoint_history", []),
            "outcome_history": state.get("outcome_history", []),
            "seq": state["seq"], "head": state["head"],
            "files": [{"path": p, "size": len(b), "sha256": sha(b), "scope": "coordinator-archive",
                       "data": encode(b)} for p, b in sorted(files.items())]}
    # Carry immutable segment lineage into snapshots that contain rolled-over history.
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
    require(isinstance(content, str) and len(content.encode()) <= DOCUMENT_LIMIT, "SIZE_LIMIT")
    matches = re.findall(r"^```json[ \t]*\n(.*?)\n```[ \t]*$", content, re.S | re.M)
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
    # Freeze the exact snapshot bytes and reject archives beyond the total budget.
    raw = canonical(snapshot)
    require(len(raw) <= MAX_ARCHIVE, "ARCHIVE_PENDING")
    digest = sha(raw)
    # Byte chunks, not file splitting, bound even one large file.
    chunks = [raw[i:i + ARCHIVE_CHUNK] for i in range(0, len(raw), ARCHIVE_CHUNK)]
    readable = "\n\n".join("File: " + item["path"] + "\n" + decode(item["data"]).decode("utf-8")
                               for item in snapshot["files"])
    width = max(1, (len(readable) + len(chunks) - 1) // len(chunks))
    parts = []
    # Pair each reconstructable chunk with readable history and enforce the document budget.
    for i, chunk in enumerate(chunks):
        body = {"schema_version": 1, "kind": "part", "snapshot": digest, "index": i,
                "count": len(chunks), "sha256": sha(chunk), "data": encode(chunk)}
        fragment = readable[i * width:(i + 1) * width]
        heading = (f"# {snapshot['issue_id']} archive part {i + 1}/{len(chunks)}\n\n"
                   "## Readable history fragment\n\n" + "\n".join("> " + line for line in fragment.splitlines()))
        content = document(body, heading)
        require(len(content.encode()) <= DOCUMENT_LIMIT, "ARCHIVE_PENDING")
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
    require(isinstance(item, dict) and set(item) == {"id", "url", "issue", "updatedAt", "content",
                                                  "origin", "request_id"}, "ARCHIVE_PENDING")
    require(item["issue"] == issue_uuid and item["origin"] == "linear_get_document", "ARCHIVE_PENDING")
    for field in ("id", "updatedAt", "request_id"):
        token(item[field])
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
    require(isinstance(observations, list) and 2 <= len(observations) <= 514, "ARCHIVE_PENDING")
    # Check read-back origins and parent identities before selecting the single root index.
    parsed = [provider_observation(x, state["issue_uuid"]) for x in observations]
    roots = [(o, p) for o, p in zip(observations, parsed) if p.get("kind") == "index"]
    require(len(roots) == 1, "ARCHIVE_PENDING")
    root_observed, root = roots[0]
    require(root.get("schema_version") == 1 and root["snapshot"] == state["export"]["snapshot"], "ARCHIVE_PENDING")
    parts = {o["id"]: (o, p) for o, p in zip(observations, parsed) if p.get("kind") == "part"}
    require(len(parts) == len(observations) - 1 == len(root["parts"]) and
            root_observed["id"] not in parts, "ARCHIVE_PENDING")
    raw = b""
    # Reconstruct in index order only from the named versions and matching part digests.
    for i, descriptor in enumerate(root["parts"]):
        require(descriptor["id"] in parts, "ARCHIVE_PENDING")
        observed, part = parts[descriptor["id"]]
        require(part["schema_version"] == 1 and part["snapshot"] == root["snapshot"] and
                part["index"] == i and part["count"] == len(parts) and
                descriptor["sha256"] == sha(canonical(part)) and
                descriptor["updatedAt"] == observed["updatedAt"], "ARCHIVE_PENDING")
        chunk = decode(part["data"], 128 * 1024)
        require(sha(chunk) == part["sha256"], "INTEGRITY_ERROR")
        raw += chunk
        require(len(raw) <= MAX_ARCHIVE, "SIZE_LIMIT")
    # Bind the reconstructed bytes to the exported snapshot and repository identity.
    require(sha(raw) == root["snapshot"], "INTEGRITY_ERROR")
    snapshot = strict_json(raw)
    require(snapshot["schema_version"] == 1 and snapshot["repo_id"] == state["repo_id"] and
            snapshot["issue_id"] == state["issue_id"] and snapshot["issue_uuid"] == state["issue_uuid"],
            "INTEGRITY_ERROR")
    files = {}
    # Validate each unique payload path, byte count, and digest before restoring its bytes.
    for item in snapshot["files"]:
        path = payload_path(item["path"], True)
        require(path not in files, "INTEGRITY_ERROR")
        data = decode(item["data"])
        require(len(data) == item["size"] and sha(data) == item["sha256"], "INTEGRITY_ERROR")
        data.decode("utf-8")
        files[path] = data
    # Require complete task content and verify the global event chain before issuing a receipt.
    require("roadmap.md" in files and "events.jsonl" in files, "INTEGRITY_ERROR")
    require(validate_history(files) == (snapshot["seq"], snapshot["head"]), "INTEGRITY_ERROR")
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
    control, task = issue.control, issue.store.task
    # Leave payloads untouched unless a durable cleanup intent names them.
    if not control.exists("cleanup.json"):
        return
    intent = control.json("cleanup.json")
    name = intent["quarantine"]
    # Move only the identity- and manifest-matching issue into its recorded quarantine.
    if task.exists(issue.id):
        require(not control.exists(name), "RECOVERY_REQUIRED")
        with task.child(issue.id, private=False) as payload:
            require(payload.identity == intent["directory_identity"] and
                    manifest(inventory(payload)) == intent["files"], "UNTRACKED_CHANGE")
        os.rename(issue.id, name, src_dir_fd=task.fd, dst_dir_fd=control.fd)
        os.fsync(task.fd)
        os.fsync(control.fd)
        fault("quarantine")
    # Resume deletion only inside the recorded quarantine with the original directory identity.
    if control.exists(name):
        with control.child(name, private=False) as payload:
            require(payload.identity == intent["directory_identity"], "UNSAFE_PATH")
            names = os.listdir(payload.fd)
            require(all(n in {"roadmap.md", "events.jsonl", "context"} or SEGMENT.fullmatch(n) or os_metadata(n) for n in names), "UNSAFE_PATH")
            remaining = {}
            # Read every remaining quarantine entry before deleting any payload bytes.
            for entry in names:
                if entry == "context":
                    with payload.child("context", private=False) as context:
                        # Validate each direct context entry without descending into arbitrary directories.
                        for note in os.listdir(context.fd):
                            remaining["context/" + note] = context.read(note, MAX_FILE)
                else:
                    remaining[entry] = payload.read(entry, MAX_FILE)
            require(all(os_metadata(p.rsplit("/", 1)[-1]) or intent["files"].get(p) == sha(b)
                        for p, b in remaining.items()), "UNTRACKED_CHANGE")
            # Unlink only entries validated in the complete remaining quarantine inventory.
            for path in sorted(remaining):
                # Delete context entries relative to their validated parent descriptor.
                if path.startswith("context/"):
                    with payload.child("context", private=False) as context:
                        context.unlink(path[8:])
                else:
                    payload.unlink(path)
                fault("delete:" + path)
            # Remove the context directory only after all validated entries have been unlinked.
            if payload.exists("context"):
                os.rmdir("context", dir_fd=payload.fd)
            os.fsync(payload.fd)
        os.rmdir(name, dir_fd=control.fd)
        os.fsync(control.fd)
    # Publish the cleaned tombstone after durable removal of the quarantined payload.
    state = intent["state"]
    state["storage"] = "cleaned"
    state["requests"] = {k: v for k, v in state["requests"].items()
                         if k == sha(intent["request_id"].encode())}
    state.pop("index_request", None)
    state.pop("checkpoint", None)
    state.pop("checkpoint_history", None)
    state.pop("outcome_history", None)
    state.pop("provenance", None)
    # Discard local export copies after the independent provider archive has been verified.
    for name in os.listdir(control.fd):
        if re.fullmatch(r"export-[0-9a-f]{64}\.json", name):
            control.read(name)  # Refuse substituted links/special files before pruning.
            control.unlink(name)
    state["tombstone"] = {"snapshot": state["export"]["snapshot"], "at": now(),
                          "generation": state["generation"], "cleanup_request": intent["request_id"]}
    control.put("state.json", state)
    fault("cleanup-state")
    control.unlink("cleanup.json")
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
    operation = request["operation"]
    key = participant_key(request)
    # Finish durable transaction recovery before evaluating this issue request.
    issue.recover()
    finish_cleanup(issue)
    state = copy.deepcopy(issue.state)
    if state:
        require(not request.get("issue_uuid") or request["issue_uuid"] == state["issue_uuid"], "ISSUE_MISMATCH")
        previous = state["requests"].get(sha(request["request_id"].encode()))
        # Replay only an identical request; a reused ID with different content is a conflict.
        if previous:
            require(previous["digest"] == sha(canonical(request)), "REQUEST_CONFLICT")
            if operation in {"create", "adopt", "attach", "resume", "bind", "restore"} and state["storage"] != "cleaned":
                store.view(issue.id)
                # Persist the committed participant generation for this worktree.
                store.save_binding(state, key)
            result = previous["result"].copy()
            reference = result.pop("result_ref", None)
            if reference:
                result.update(issue.control.json(reference))
            return result
    if operation == "diagnose":
        return {"ok": True, "code": "PRESENT" if state else "ABSENT", "repo_id": store.registration["repo_id"],
                "issue_id": issue.id, "revision": state["revision"] if state else None,
                "storage": state["storage"] if state else "absent"}
    # Read the existing session binding; never infer it from the prompt.
    binding = store.binding()
    if operation in {"create", "adopt", "attach", "resume", "bind"}:
        require(not binding or binding["issue_id"] == issue.id, "BINDING_CONFLICT")
    # Require explicit adoption for existing payloads and explicit creation for absent ones.
    if not state:
        require(operation in {"create", "adopt"}, "BINDING_MISSING")
        exists = store.task.exists(issue.id)
        require(exists == (operation == "adopt"), "ADOPTION_REQUIRED" if exists else "RECOVERY_REQUIRED")
        state = new_state(store, request)
        if operation == "adopt":
            evidence(request.get("evidence"))
            with store.task.child(issue.id, private=False) as payload:
                files = inventory(payload)
            require(request.get("inventory") == manifest(files), "UNTRACKED_CHANGE")
            owners = request.get("owners", {})
            require(set(owners) == {p for p in files if p != "events.jsonl" and not SEGMENT.fullmatch(p)} and owners["roadmap.md"] == key,
                    "NOT_OWNER")
            require(all(DIGEST.fullmatch(v) for v in owners.values()), "NOT_OWNER")
            state["owners"] = owners
            state["adoption"] = {"inventory": manifest(files), "evidence": request["evidence"]}
            state["seq"], state["head"] = validate_history(files)
        else:
            # Initialize a new roadmap from the repository template and start an empty event stream.
            template = Path(__file__).resolve().parents[2] / "core/templates/task-workspace/roadmap.md"
            files = {"roadmap.md": template.read_text().replace("{{issue_id}}", issue.id).encode(),
                     "events.jsonl": b""}
        result = attach(state, request)
        # Publish this lifecycle result through the recoverable transaction.
        result = issue.commit(state, files, request, result, operation)
        store.view(issue.id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(issue.state, key)
        return result
    # Reconstruct only the tombstone-matching archive before reopening a new generation.
    if operation == "restore":
        require(state["storage"] == "cleaned" and state.get("tombstone"), "RECOVERY_REQUIRED")
        require(key == state["coordinator"], "NOT_OWNER")
        snapshot, files, receipt = verify_provider(state, request.get("observations"))
        require(receipt["snapshot"] == state["tombstone"]["snapshot"] and
                manifest(files) == state["files"], "INTEGRITY_ERROR")
        require(not store.task.exists(issue.id), "RECOVERY_REQUIRED")
        state["history"] = state.get("history", []) + [state["tombstone"]]
        state["storage"], state["disposition"] = "present", "active"
        state["provenance"] = snapshot.get("provenance", {})
        state["checkpoint"] = snapshot.get("checkpoint")
        state["event_segments"] = snapshot.get("event_segments", [])
        state["adoption"] = snapshot.get("adoption")
        state["checkpoint_history"] = snapshot.get("checkpoint_history", [])
        state["outcome_history"] = snapshot.get("outcome_history", [])
        state["generation"] += 1
        for participant in state["participants"].values():
            participant.update(status="detached", ack=None, generation=participant["generation"] + 1)
        state["participants"][key]["status"] = "attached"
        state.pop("tombstone")
        # A durable initialization transaction restores the exact history before the reopen event.
        old = issue.state
        issue.state = None
        try:
            result = issue.commit(state, files, request,
                                  {"binding_generation": state["participants"][key]["generation"]}, "restore")
        except BaseException:
            issue.state = old
            raise
        store.view(issue.id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(issue.state, key)
        return result
    # Import inspected Markdown while refusing any change to committed event history.
    if operation == "reconcile-files":
        require(state["disposition"] not in TERMINAL, "TERMINAL")
        require(binding and binding["issue_id"] == issue.id and
                binding["binding_generation"] == request.get("binding_generation"), "BINDING_MISSING")
        authorize(state, request, coordinator=True)
        expected(state, request)
        evidence(request.get("evidence"))
        with store.task.child(issue.id, private=False) as payload:
            require(payload.identity == state["directory_identity"], "UNSAFE_PATH")
            observed = inventory(payload)
        require(manifest(observed) == request.get("inventory"), "UNTRACKED_CHANGE")
        require(set(observed) == set(state["files"]) and
                all(sha(b) == state["files"][p] for p, b in observed.items()
                    if p == "events.jsonl" or SEGMENT.fullmatch(p)), "INTEGRITY_ERROR")
        # Explicit reconciliation imports inspected Markdown only, never arbitrary events.
        issue.state = copy.deepcopy(state)
        issue.state["files"] = manifest(observed)
        state["reconciliation"] = {"previous": state["files"], "evidence": request["evidence"]}
        return issue.commit(state, observed, request, {}, "reconciliation")
    files = issue.files()
    store.view(issue.id)
    if operation in {"create", "attach", "resume", "bind"}:
        result = attach(state, request)
        # Publish this lifecycle result through the recoverable transaction.
        result = issue.commit(state, files, request, result, "attach")
        store.view(issue.id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(issue.state, key)
        return result
    require(binding and binding["issue_id"] == issue.id and binding["binding_generation"] == request.get("binding_generation"),
            "BINDING_MISSING")
    maintenance = operation in {"archive-prepare", "archive-index", "archive-verify", "archive-save-start", "archive-observe-save", "cleanup-plan", "cleanup-commit", "reopen"}
    key, participant = authorize(state, request, maintenance=maintenance)
    result = {}
    event_type = operation
    if operation in {"scope", "update", "checkpoint", "outcome", "transfer-coordinator", "reconcile-participant",
                     "archive-prepare", "event-rollover", "reopen"}:
        prepared = (operation == "archive-prepare" and sha((request["request_id"] + ":prepare").encode()) in state["requests"])
        if not prepared:
            expected(state, request)
    if operation not in {"read", "ready", "diagnose", "archive-prepare", "archive-index", "archive-verify",
                         "cleanup-plan", "cleanup-commit", "detach", "reopen", "archive-save-start", "archive-observe-save"}:
        require(state["disposition"] not in TERMINAL, "TERMINAL")
    if operation in {"scope", "checkpoint", "outcome", "transfer-coordinator", "reconcile-participant",
                     "archive-prepare", "archive-index", "archive-verify", "archive-save-start", "archive-observe-save", "cleanup-plan", "cleanup-commit", "event-rollover", "reopen"}:
        authorize(state, request, coordinator=True, maintenance=maintenance)
    # Assign reader-specific sources and invalidate any prior acknowledgment.
    if operation == "scope":
        target = request["target_participant"]
        require(DIGEST.fullmatch(target), "SCOPE_MISSING")
        packet = validate_packet(request["packet"])
        require(all(ref["reader"] == target for ref in packet), "SCOPE_MISSING")
        state.setdefault("assignments", {})[target] = {"packet": packet}
        # Update existing scope without reviving a detached generation.
        if target in state["participants"]:
            target_state = state["participants"][target]
            target_state.update(packet=packet, ack=None)
            if target_state["status"] != "detached":
                target_state["status"] = "attached"
        for path in request.get("owned_paths", []):
            payload_path(path)
            require(path != "roadmap.md" and state["owners"].get(path, target) == target, "NOT_OWNER")
            state["owners"][path] = target
        result["packet_digest"] = sha(canonical(packet))
    elif operation in {"read", "acknowledge", "ready"}:
        refs = packet_reads(state, participant, files)
        digest = sha(canonical(participant["packet"]))
        if operation == "acknowledge":
            require(request.get("packet_digest") == digest, "SOURCE_STALE")
            participant.update(ack=digest, status="ready")
        elif operation == "ready":
            require(state["disposition"] not in TERMINAL and participant["ack"] == digest and
                    participant["status"] == "ready", "NOT_READY")
        if operation != "acknowledge":
            return {"ok": True, "code": "OK", "revision": state["revision"], "references": refs,
                    "packet_digest": digest, "binding_generation": participant["generation"]}
        result.update(references=refs, packet_digest=digest)
    # Require file ownership, the current content digest, and attributable replacement content.
    elif operation == "update":
        path = payload_path(request["path"])
        require(state["owners"].get(path) == key, "NOT_OWNER")
        require(request.get("old_digest") == state["files"].get(path), "REVISION_CONFLICT")
        data = request["content"].encode("utf-8")
        require(len(data) <= MAX_FILE, "SIZE_LIMIT")
        provenance = request["provenance"]
        require(set(provenance) == {"sources", "applicability", "status"}, "EVIDENCE_REQUIRED")
        evidence(provenance["sources"])
        token(provenance["applicability"])
        token(provenance["status"])
        state["provenance"][path] = {**provenance, "author": key, "supersedes": state["revision"]}
        files[path] = data
    # Retain the bounded handoff record and evidence before marking a submitted PR in review.
    elif operation == "checkpoint":
        checkpoint = request["checkpoint"]
        require(set(checkpoint) == {"goal", "constraints", "sources", "progress", "blockers", "handoff", "candidate"},
                "EVIDENCE_REQUIRED")
        require(len(canonical(checkpoint)) <= 16384, "SIZE_LIMIT")
        evidence(checkpoint["sources"])
        state.setdefault("checkpoint_history", []).append(checkpoint)
        state["checkpoint"] = checkpoint
        if request.get("submitted_pr"):
            require(str(request["submitted_pr"]).startswith("https://"), "EVIDENCE_REQUIRED")
            state["disposition"] = "in_review"
            state["submitted_pr"] = request["submitted_pr"]
    # Require outcome evidence without bypassing pending work or completion obligations.
    elif operation == "outcome":
        disposition = request["disposition"]
        require(disposition in TERMINAL | {"active", "blocked", "in_review"})
        evidence(request.get("evidence"))
        # Prevent terminal state from stranding recorded pending operations.
        if disposition in TERMINAL:
            require(not any(p["pending"] for p in state["participants"].values()), "PENDING_OPERATION")
        # Verify acceptance, merge, and obligation evidence plus the matching provider completion.
        if disposition == "completed":
            require(set(request.get("completion", {})) == {"human_acceptance", "merge", "obligations"}, "EVIDENCE_REQUIRED")
            for refs in request["completion"].values():
                evidence(refs)
                for ref in refs:
                    path = Path(ref["locator"])
                    require(path.is_absolute(), "EVIDENCE_REQUIRED")
                    with Directory.absolute(path.parent) as source:
                        require(sha(source.read(path.name, MAX_FILE)) == ref["sha256"], "SOURCE_STALE")
            provider = request.get("provider_status", {})
            require(set(provider) == {"origin", "issue_uuid", "status_type", "completed_at", "request_id"}, "EVIDENCE_REQUIRED")
            require(provider["origin"] == "linear_get_issue" and provider["issue_uuid"] == state["issue_uuid"]
                    and provider["status_type"] == "completed" and provider["completed_at"], "EVIDENCE_REQUIRED")
            token(provider["request_id"])
            state["provider_completion"] = provider
        if disposition == "failed":
            require(request.get("abandoned") is True, "EVIDENCE_REQUIRED")
        state["disposition"] = disposition
        state["outcome"] = {"disposition": disposition, "evidence": request["evidence"],
                            "completion": request.get("completion"), "at": now()}
        state.setdefault("outcome_history", []).append(state["outcome"])
    # Accept only the bounded event vocabulary; arbitrary payload content is not an event.
    elif operation == "event":
        require(request.get("event_type") in {"check", "tool-start", "tool-complete", "observation"})
        require(set(request.get("event", {})) <= {"code"}, "INVALID_REQUEST")
        require(request.get("event", {}).get("code", "OK") in {"OK", "FAILED", "UNKNOWN", "INTERRUPTED"})
        event_type = request["event_type"]
    # Recheck the acknowledged source packet before admitting new pending work.
    elif operation == "tool-start":
        packet_reads(state, participant, files)
        require(participant["ack"] == sha(canonical(participant["packet"])) and participant["status"] == "ready", "NOT_READY")
        tool = token(request["tool_id"])
        require(tool not in participant["pending"], "REQUEST_CONFLICT")
        # Associate an observed polling transport without borrowing another participant's work.
        pending = {"status": "pending"}
        if request.get("poll_handle") is not None:
            handle = token(request["poll_handle"])
            matches = [identifier for identifier, entry in participant["pending"].items()
                       if entry.get("handle") == handle]
            require(len(matches) == 1, "UNKNOWN_OPERATION")
            pending.update(poll_parent=matches[0], poll_handle=handle)
        # Reserve and persist the transport independently from its original process.
        participant["pending"][tool] = pending
    # Validate observation identity before settling or retaining asynchronous work.
    elif operation == "tool-complete":
        # Resolve the observed call ID and validate optional handle metadata first.
        observed_tool = token(request["tool_id"])
        tool = observed_tool
        handle = token(request["async_handle"]) if request.get("async_handle") is not None else None
        transport = None
        if request.get("poll"):
            # A poll must identify one original process, or its recorded completed parent.
            require(handle is not None, "UNKNOWN_OPERATION")
            matches = [identifier for identifier, entry in participant["pending"].items()
                       if entry.get("handle") == handle]
            transport = participant["pending"].get(observed_tool)
            if transport is not None:
                # Validate the pre-hook's explicit relationship, including concurrent final polls.
                require(transport.get("poll_handle") == handle and transport.get("poll_parent"),
                        "REQUEST_CONFLICT")
                tool = transport["poll_parent"]
                expected_matches = [tool] if tool in participant["pending"] else []
                require(matches == expected_matches, "UNKNOWN_OPERATION")
            else:
                # Hosts that omit polling pre-hooks still require unique participant-local correlation.
                require(len(matches) == 1, "UNKNOWN_OPERATION")
                tool = matches[0]
        # An already settled parent is allowed only through its still-recorded poll transport.
        require(tool in participant["pending"] or transport is not None, "UNKNOWN_OPERATION")
        if tool in participant["pending"]:
            pending = participant["pending"][tool]
            if handle is not None and pending.get("handle") is not None:
                require(pending["handle"] == handle, "REQUEST_CONFLICT")
            # Explicit process completion wins only after the handle and relationship checks.
            if request.get("completed") is True:
                del participant["pending"][tool]
            elif handle is not None:
                # A repeated process observation is event-free only when no transport is retiring.
                if pending["status"] == "unknown" and transport is None:
                    event_type = None
                participant["pending"][tool] = {"status": "unknown", "handle": handle}
            else:
                raise WorkspaceError("UNKNOWN_OPERATION")
        # The returned polling call is finished even when its original process is still running.
        if transport is not None:
            del participant["pending"][observed_tool]
    # Require the exact participant generation and no pending work before detachment.
    elif operation in {"detach", "reconcile-participant"}:
        target = key if operation == "detach" else request["target_participant"]
        member = state["participants"][target]
        require(request.get("target_generation", request.get("binding_generation")) == member["generation"], "STALE_BINDING")
        evidence(request.get("evidence"))
        require(not member["pending"], "PENDING_OPERATION")
        member["status"], member["ack"] = "detached", None
    # Transfer roadmap ownership with the coordinator role and retain the supplied evidence.
    elif operation == "transfer-coordinator":
        target = request["target_participant"]
        require(target in state["participants"] and not participant["pending"], "PENDING_OPERATION")
        evidence(request.get("evidence"))
        state["coordinator"] = target
        state["owners"]["roadmap.md"] = target
    # Reopen terminal work only with evidence and require a fresh packet acknowledgment.
    elif operation == "reopen":
        evidence(request.get("evidence"))
        require(state["disposition"] in TERMINAL, "INVALID_REQUEST")
        state["disposition"] = "active"
        participant.update(status="attached", ack=None)
    # Rotate only bytes covered by the current verified provider checkpoint.
    elif operation == "event-rollover":
        require(not any(p["pending"] for p in state["participants"].values()), "PENDING_OPERATION")
        receipt, export = state.get("archive"), state.get("export")
        require(receipt and export and receipt["snapshot"] == export["snapshot"] and
                export["files"] == manifest(files) and export["revision"] == state["revision"], "ARCHIVE_PENDING")
        data = files["events.jsonl"]
        require(data, "ARCHIVE_PENDING")
        first = strict_json(data.splitlines()[0])["seq"]
        name = f"events-{first:012d}-{state['seq']:012d}.jsonl"
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
        require(not any(p["pending"] for p in state["participants"].values()), "PENDING_OPERATION")
        if request.get("seal"):
            require(state["disposition"] in TERMINAL and all(
                k == key or p["status"] == "detached" for k, p in state["participants"].items()), "RETAINED")
            participant["status"] = "maintenance"
        # Commit seal/preparation event BEFORE the frozen snapshot.
        prepare_request = {**request, "request_id": request["request_id"] + ":prepare"}
        prior = state["requests"].get(sha(prepare_request["request_id"].encode()))
        if prior:
            require(prior["digest"] == sha(canonical(prepare_request)), "REQUEST_CONFLICT")
        else:
            # Publish this lifecycle result through the recoverable transaction.
            issue.commit(state, files, prepare_request, {}, "archive-prepare")
        state = copy.deepcopy(issue.state)
        files = issue.files()
        snapshot = archive_payload(state, files)
        export = export_documents(snapshot)
        state["export"] = {"snapshot": export["snapshot"], "revision": state["revision"],
                           "files": manifest(files), "request_id": request["request_id"]}
        require(len(canonical(export)) <= MAX_REQUEST, "ARCHIVE_PENDING")
        issue.control.put("export-" + export["snapshot"] + ".json", export)
        return issue.commit(state, files, request, export)
    # Build the index only from matching independent part identities and versions.
    elif operation == "archive-index":
        export = issue.control.json("export-" + state["export"]["snapshot"] + ".json")
        observations = request["observations"]
        require(len(observations) == len(export["parts"]), "ARCHIVE_PENDING")
        descriptors = []
        for observed, wanted in zip(observations, export["parts"]):
            part = provider_observation(observed, state["issue_uuid"])
            require(part == parse_document(wanted["content"]), "ARCHIVE_PENDING")
            descriptors.append({"id": observed["id"], "updatedAt": observed["updatedAt"], "sha256": sha(canonical(part))})
        require(len({d["id"] for d in descriptors}) == len(descriptors), "ARCHIVE_PENDING")
        root = {"schema_version": 1, "kind": "index", "snapshot": export["snapshot"], "parts": descriptors}
        content = document(root, f"# {issue.id} verified archive index\n\nDisposition: {state['disposition']}.\n"
                           "Reconstructable roadmap, context and event history are in the numbered parts.\n"
                           "This archive is evidence, not human acceptance or execution authority.")
        result = {"issue": state["issue_uuid"], "title": f"{issue.id} snapshot {export['snapshot']} index", "content": content}
        require(len(content.encode()) <= DOCUMENT_LIMIT, "ARCHIVE_PENDING")
        state["index_request"] = result.copy()
        event_type = None
    # Track save uncertainty by immutable content digest to prevent blind duplicate saves.
    elif operation in {"archive-save-start", "archive-observe-save"}:
        export = issue.control.json("export-" + state["export"]["snapshot"] + ".json")
        documents = export["parts"] + ([state["index_request"]] if state.get("index_request") else [])
        digest = request["content_digest"]
        require(digest in {sha(doc["content"].encode()) for doc in documents}, "ARCHIVE_PENDING")
        saves = state.setdefault("provider_saves", {})
        if operation == "archive-save-start":
            require(digest not in saves, "ARCHIVE_PENDING", "Uncertain save: locate and read the matching document before another save.")
            saves[digest] = {"status": "uncertain"}
        else:
            document_id = token(request["document_id"])
            existing = saves.get(digest, {})
            require(not existing.get("id") or existing["id"] == document_id, "ARCHIVE_PENDING")
            saves[digest] = {"status": "readback-required", "id": document_id}
        event_type = None
    # Require provider bytes and revision to match the current local export exactly.
    elif operation in {"archive-verify", "cleanup-commit"}:
        snapshot, archived, receipt = verify_provider(state, request.get("observations"))
        require(manifest(archived) == manifest(files) == state["export"]["files"] and
                snapshot["revision"] == state["revision"] == state["export"]["revision"], "ARCHIVE_PENDING")
        if operation == "archive-verify":
            state["archive"] = receipt
            result = {"receipt": receipt}
            event_type = None
        else:
            eligible(state)
            require(receipt["root"]["id"] == state["archive"]["root"]["id"] and
                    receipt["root"]["updatedAt"] == state["archive"]["root"]["updatedAt"], "ARCHIVE_PENDING")
            # Require fresh read-backs bearing this cleanup challenge before recording deletion intent.
            challenge = request.get("cleanup_challenge")
            require(challenge and challenge == state.get("cleanup_challenge") and
                    all(o["request_id"] == challenge for o in request["observations"]), "ARCHIVE_PENDING")
            state["archive"] = receipt
            result = {"ok": True, "code": "CLEANED", "revision": state["revision"], "issue_id": issue.id}
            state["requests"][sha(request["request_id"].encode())] = {"digest": sha(canonical(request)), "result": result}
            intent = {"state": state, "files": state["files"], "directory_identity": state["directory_identity"],
                      "quarantine": "quarantine-" + uuid.uuid4().hex, "request_id": request["request_id"]}
            issue.control.put("cleanup.json", intent)
            fault("cleanup-intent")
            finish_cleanup(issue)
            return result
    # Issue a new challenge for independent read-backs of the verified archive.
    elif operation == "cleanup-plan":
        eligible(state)
        state["cleanup_challenge"] = str(uuid.uuid4())
        result = {"code": "READBACK_REQUIRED", "cleanup_challenge": state["cleanup_challenge"],
                  "root": state["archive"]["root"], "parts": state["archive"]["parts"]}
        event_type = None
    else:
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
    old_id, new_id = issue_id(request["issue_id"]), issue_id(request["new_issue_id"])
    require(old_id != new_id, "BINDING_CONFLICT")
    key = participant_key(request)
    evidence(request.get("evidence"))
    with ExitStack() as stack:
        controls = {}
        # Lock both issues in stable order before retiring the old binding or attaching the new one.
        for identifier in sorted([old_id, new_id]):
            control = stack.enter_context(store.issues.child(identifier))
            stack.enter_context(control.lock())
            controls[identifier] = control
        old, new = Issue(store, controls[old_id], old_id), Issue(store, controls[new_id], new_id)
        old.recover()
        new.recover()
        require(old.state and new.state, "RECOVERY_REQUIRED")
        old_files, new_files = old.files(), new.files()
        old_state, new_state_value = copy.deepcopy(old.state), copy.deepcopy(new.state)
        member = old_state["participants"].get(key)
        require(member and member["generation"] == request["binding_generation"], "STALE_BINDING")
        require(not member["pending"], "PENDING_OPERATION")
        require(key != old_state["coordinator"] or all(k == key or p["status"] == "detached"
                for k, p in old_state["participants"].items()), "PENDING_OPERATION")
        # Build the request from explicit caller or observed session identities.
        target_request = {**request, "operation": "rebind", "issue_id": new_id, "old_issue_id": old_id,
                          "binding_generation": request.get("new_binding_generation")}
        previous = new_state_value["requests"].get(sha(request["request_id"].encode()))
        # Replay only an identical request; a reused ID with different content is a conflict.
        if previous:
            require(previous["digest"] == sha(canonical(target_request)), "REQUEST_CONFLICT")
            store.view(new_id)
            # Persist the committed participant generation for this worktree.
            store.save_binding(new.state, key)
            return previous["result"]
        result = attach(new_state_value, target_request)
        # Retire the old participant before publishing the new binding; retries can finish publication.
        member.update(status="detached", ack=None)
        if member != old.state["participants"][key]:
            old.commit(old_state, old_files, {**request, "request_id": request["request_id"] + ":retire"}, {}, "rebind")
        # Publish this lifecycle result through the recoverable transaction.
        result = new.commit(new_state_value, new_files, target_request, result, "rebind")
        store.view(new_id)
        # Persist the committed participant generation for this worktree.
        store.save_binding(new.state, key)
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
    candidates = request["cleanup_candidates"]
    require(isinstance(candidates, list) and len(candidates) <= 16)
    results = []
    # Attempt only explicitly listed issues, each with its own maintenance session.
    for candidate in candidates:
        identifier = issue_id(candidate["issue_id"])
        require(identifier != request["issue_id"], "INVALID_REQUEST")
        # A separate bound maintenance session avoids retiring the new task's owner.
        cleanup = {**candidate, "schema_version": 1, "operation": "cleanup-commit",
                   "request_id": request["request_id"] + ":collect:" + identifier,
                   "worktree": request["worktree"], "repo_id": request["repo_id"]}
        require(cleanup.get("session_id") != request["session_id"], "BINDING_CONFLICT")
        results.append({"issue_id": identifier, **execute(cleanup)})
    return results



def permission_paths(request):
    """Derive narrow issue/control paths for a failed filesystem permission request.

    Args:
        request: Versioned lifecycle request with explicit repository, issue and session identities.

    Returns:
        The paths relevant to registration or the requested issue.
    """
    root = Path(request.get("worktree", "/"))
    try:
        root = repository(root)[0]
    except (OSError, WorkspaceError, subprocess.SubprocessError):
        pass
    main = request.get("main_worktree")
    if main is None:
        try:
            with Directory.absolute(root) as directory, directory.child(".task") as local:
                main = local.json(".repository.json")["main"]
        except (OSError, WorkspaceError, KeyError):
            pass
    paths = [str(root / ".task/.repository.json"), str(root / ".task/.bindings")]
    if main:
        store = Path(main) / ".task"
        paths.append(str(store / ".control/repository.json"))
        identifier = request.get("issue_id")
        if isinstance(identifier, str) and ISSUE.fullmatch(identifier):
            paths.extend([str(store / identifier), str(store / ".control/issues" / identifier)])
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
    try:
        require(isinstance(request, dict) and len(canonical(request)) <= MAX_REQUEST)
        require(request.get("schema_version") == 1)
        token(request["request_id"])
        operation = request["operation"]
        if operation == "register":
            require("repo_id" not in request, "INVALID_REQUEST")
            return register(request)
        # Allow registration discovery before the caller knows the repository ID.
        if operation == "diagnose" and "repo_id" not in request:
            root, _, _ = repository(request["worktree"])
            with Directory.absolute(root) as directory:
                # Validate any existing entry before reusing or replacing it.
                if not directory.exists(".task"):
                    return {"ok": True, "code": "REGISTRATION_REQUIRED"}
                with directory.child(".task") as local:
                    # Validate any existing entry before reusing or replacing it.
                    if not local.exists(".repository.json"):
                        return {"ok": True, "code": "REGISTRATION_REQUIRED"}
                    # Read repository registration through validated directory handles.
                    reg = local.json(".repository.json")
                    return {"ok": True, "code": "REGISTERED", "repo_id": reg["repo_id"]}
        identifier = issue_id(request["issue_id"])
        token(request["repo_id"])
        participant_key(request)
        # Serialize session-binding changes before acquiring the target issue lock.
        with Store(request) as store, store.bindings.lock(participant_key(request) + ".lock"):
            if operation == "rebind":
                return rebind(store, request)
            with store.issues.child(identifier, True) as control, control.lock():
                result = operate(store, Issue(store, control, identifier), request)
            if result["ok"] and operation == "create" and request.get("cleanup_candidates"):
                result["collection"] = collect_candidates(store, request)
            return result
    except WorkspaceError as error:
        return {"ok": False, "code": error.code, "action": error.action}
    # Return only the narrow paths needed to retry the denied filesystem operation.
    except PermissionError as error:
        paths = permission_paths(request)
        return {"ok": False, "code": "PERMISSION_REQUIRED", "paths": paths,
                "operation": request.get("operation"),
                "action": "Grant only the listed issue/control and binding paths, then retry this operation."}
    # Retain data when filesystem or Git checks fail; require explicit recovery.
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "code": "RECOVERY_REQUIRED", "action": "Inspect filesystem identity and unfinished transactions; retain all data."}
    # Translate malformed requests without exposing exception contents.
    except (KeyError, TypeError, ValueError, UnicodeError, AttributeError):
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
    try:
        # Select the explicit argument or bounded standard input, then parse one request.
        raw = args.request_json.encode() if args.request_json is not None else sys.stdin.buffer.read(MAX_REQUEST + 1)
        require(len(raw) <= MAX_REQUEST, "SIZE_LIMIT")
        result = execute(strict_json(raw))
    except WorkspaceError as error:
        result = {"ok": False, "code": error.code}
    print(canonical(result).decode())
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
