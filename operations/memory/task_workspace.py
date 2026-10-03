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

MAX_REQUEST = 48 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_EVENTS = 16 * 1024 * 1024
MAX_ARCHIVE = 32 * 1024 * 1024
DOCUMENT_LIMIT = 256 * 1024
ARCHIVE_CHUNK = 64 * 1024
ISSUE = re.compile(r"[A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,9}", re.ASCII)
NOTE = re.compile(r"context/[a-z0-9][a-z0-9._-]{0,79}\.md", re.ASCII)
DIGEST = re.compile(r"[0-9a-f]{64}")
TERMINAL = {"completed", "cancelled", "failed"}
FAILPOINT = None  # Test-only callable; never accepted in requests or environment.


class WorkspaceError(Exception):
    def __init__(self, code, action="Inspect the assigned workspace; preserve its bytes."):
        self.code, self.action = code, action
        super().__init__(code)


def require(condition, code="INVALID_REQUEST", action=None):
    if not condition:
        raise WorkspaceError(code, action or "Inspect the request and retry with explicit identities.")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def fault(point):
    if FAILPOINT:
        FAILPOINT(point)


def token(value):
    require(isinstance(value, str) and 0 < len(value) <= 160 and
            all(32 < ord(c) < 127 for c in value))
    return value


def issue_id(value):
    require(isinstance(value, str) and ISSUE.fullmatch(value), "INVALID_ISSUE")
    return value


def payload_path(value, events=False):
    require(isinstance(value, str) and ".." not in value and
            (value == "roadmap.md" or NOTE.fullmatch(value) or
             (events and value == "events.jsonl")), "UNSAFE_PATH")
    return value


def decode(value, limit=MAX_FILE):
    require(isinstance(value, str) and len(value) <= (limit + 2) // 3 * 4, "SIZE_LIMIT")
    try:
        result = base64.b64decode(value, validate=True)
    except ValueError:
        raise WorkspaceError("INTEGRITY_ERROR") from None
    require(len(result) <= limit, "SIZE_LIMIT")
    return result


def encode(value):
    return base64.b64encode(value).decode("ascii")


def strict_json(data):
    def pairs(items):
        result = {}
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
    """An owned, pinned directory descriptor; children never follow links."""
    def __init__(self, fd, private=False):
        self.fd = fd
        s = os.fstat(fd)
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == os.getuid(), "UNSAFE_PATH")
        require(not private or not s.st_mode & 0o022, "UNSAFE_PATH")
        self.identity = [s.st_dev, s.st_ino]

    @classmethod
    def absolute(cls, path):
        path = Path(path)
        require(path.is_absolute() and ".." not in path.parts, "UNSAFE_PATH")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for name in path.parts[1:]:
                nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            return cls(fd)
        except BaseException:
            os.close(fd)
            raise

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def exists(self, name):
        try:
            os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def child(self, name, create=False, private=True):
        require("/" not in name and name not in {"", ".", ".."}, "UNSAFE_PATH")
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=self.fd)
                os.fsync(self.fd)
            except FileExistsError:
                pass
        return Directory(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=self.fd), private)

    def read(self, name, limit=MAX_REQUEST):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            s = os.fstat(fd)
            require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and
                    s.st_uid == os.getuid() and not s.st_mode & 0o022, "UNSAFE_PATH")
            require(s.st_size <= limit, "SIZE_LIMIT")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                result = stream.read(limit + 1)
            require(len(result) <= limit, "SIZE_LIMIT")
            return result
        finally:
            os.close(fd)

    def json(self, name):
        return strict_json(self.read(name))

    def write(self, name, data):
        require("/" not in name and name not in {"", ".", ".."}, "UNSAFE_PATH")
        if self.exists(name):
            self.read(name)  # Refuse links/special files before replacement.
        temp = ".write-" + uuid.uuid4().hex
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
            os.replace(temp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            os.close(fd)
            if self.exists(temp):
                os.unlink(temp, dir_fd=self.fd)

    def put(self, name, value):
        self.write(name, canonical(value))

    def unlink(self, name):
        os.unlink(name, dir_fd=self.fd)
        os.fsync(self.fd)

    @contextmanager
    def lock(self, name="lock", timeout=1.0):
        end = time.monotonic() + timeout
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
            end = time.monotonic() + timeout
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
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                            text=True, timeout=2, check=False)
    require(result.returncode == 0, "REPOSITORY_MISMATCH")
    return result.stdout.strip()


def repository(path):
    root = Path(git(path, "rev-parse", "--show-toplevel"))
    require(git(root, "rev-parse", "--is-bare-repository") == "false", "HOST_UNSUPPORTED")
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    trees = [Path(line[9:]) for line in git(root, "worktree", "list", "--porcelain").splitlines()
             if line.startswith("worktree ")]
    require(root in trees, "REPOSITORY_MISMATCH")
    return root, common, trees


def fs_identity(path):
    with Directory.absolute(path) as directory:
        return directory.identity


def register(request):
    root, common, trees = repository(request["worktree"])
    main = Path(request["main_worktree"])
    require(main == trees[0] and main in trees, "REPOSITORY_MISMATCH")
    require(repository(main)[1] == common, "REPOSITORY_MISMATCH")
    identity = {"schema_version": 1, "common": str(common), "main": str(main),
                "common_identity": fs_identity(common), "main_identity": fs_identity(main)}
    with Directory.absolute(main) as directory, directory.child(".task", True) as task, \
            task.child(".control", True) as control, control.lock("registration.lock"):
        if control.exists("repository.json"):
            existing = control.json("repository.json")
            require(all(existing[k] == v for k, v in identity.items()), "REPOSITORY_MISMATCH")
            identity = existing
        else:
            identity["repo_id"] = str(uuid.uuid4())
            control.put("repository.json", identity)
        with Directory.absolute(root) as worktree, worktree.child(".task", True) as local:
            if local.exists(".repository.json"):
                require(local.json(".repository.json") == identity, "REPOSITORY_MISMATCH")
            local.put(".repository.json", identity)
    return {"ok": True, "code": "REGISTERED", **identity}


class Store:
    def __init__(self, request):
        self.request = request
        self.root, common, trees = repository(request["worktree"])
        self.handles = []
        try:
            self.local_root = self.keep(Directory.absolute(self.root))
            self.local = self.keep(self.local_root.child(".task"))
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
        self.handles.append(directory)
        return directory

    def close(self):
        for directory in reversed(self.handles):
            directory.close()
        self.handles = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def binding(self):
        name = participant_key(self.request) + ".json"
        return self.bindings.json(name) if self.bindings.exists(name) else None

    def save_binding(self, state, participant):
        self.bindings.put(participant + ".json", {
            "repo_id": self.registration["repo_id"], "issue_id": state["issue_id"],
            "participant_id": participant, "binding_generation": state["participants"][participant]["generation"]})

    def view(self, issue):
        if str(self.root) == self.registration["main"]:
            return
        target = str(Path(self.registration["main"]) / ".task" / issue)
        if not self.local.exists(issue):
            try:
                os.symlink(target, issue, dir_fd=self.local.fd)
                os.fsync(self.local.fd)
            except FileExistsError:
                pass
        s = os.stat(issue, dir_fd=self.local.fd, follow_symlinks=False)
        require(stat.S_ISLNK(s.st_mode) and os.readlink(issue, dir_fd=self.local.fd) == target,
                "UNSAFE_PATH")
        with self.task.child(issue):
            pass


def participant_key(request):
    return sha(canonical([token(request.get("host", "codex")), token(request["session_id"])]))


def inventory(directory, require_roadmap=True):
    result = {}
    for name in os.listdir(directory.fd):
        if name == "context":
            with directory.child(name, private=False) as context:
                for note in os.listdir(context.fd):
                    path = payload_path("context/" + note)
                    result[path] = context.read(note, MAX_FILE)
        else:
            payload_path(name, events=True)
            result[name] = directory.read(name, MAX_FILE)
    require(not require_roadmap or "roadmap.md" in result, "RECOVERY_REQUIRED")
    return result


def manifest(files):
    return {path: sha(data) for path, data in sorted(files.items())}


def write_payload(directory, path, data):
    payload_path(path, events=True)
    if path.startswith("context/"):
        with directory.child("context", True, private=False) as context:
            context.write(path[8:], data)
    else:
        directory.write(path, data)


def validate_events(data):
    require(not data or data.endswith(b"\n"), "INTEGRITY_ERROR")
    prior = None
    seq = 0
    for line in data.splitlines():
        require(len(line) <= 8192, "INTEGRITY_ERROR")
        event = strict_json(line)
        digest = event.pop("digest", None)
        require(event["seq"] == seq + 1 and event["prev_digest"] == prior and
                sha(canonical(event)) == digest, "INTEGRITY_ERROR")
        prior, seq = digest, seq + 1
    return seq, prior


class Issue:
    def __init__(self, store, control, identifier):
        self.store, self.control, self.id = store, control, identifier
        self.state = control.json("state.json") if control.exists("state.json") else None

    def files(self):
        require(self.state and self.state["storage"] != "cleaned", "RECOVERY_REQUIRED")
        with self.store.task.child(self.id, private=False) as payload:
            require(payload.identity == self.state["directory_identity"], "UNSAFE_PATH")
            result = inventory(payload)
        if manifest(result) != self.state["files"]:
            data = result.get("events.jsonl", b"")
            end = data.rfind(b"\n") + 1
            prefix, tail = data[:end], data[end:]
            corrected = {**result, "events.jsonl": prefix}
            if tail and manifest(corrected) == self.state["files"]:
                self.control.write("interrupted-tail-" + sha(tail), tail)
                with self.store.task.child(self.id, private=False) as payload:
                    payload.write("events.jsonl", prefix)
                result = corrected
                self.control.put("diagnostic-loss.json", {"discarded_tail_bytes": len(tail), "at": now()})
            else:
                raise WorkspaceError("UNTRACKED_CHANGE")
        seq, head = validate_events(result.get("events.jsonl", b""))
        require(seq == self.state["seq"] and head == self.state["head"], "INTEGRITY_ERROR")
        return result

    def recover(self):
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
            for path, data in present.items():
                old = (tx["base"] or {}).get(path)
                require(sha(data) in {old, state["files"][path]}, "UNTRACKED_CHANGE")
            for path, data in tx["writes"].items():
                write_payload(payload, path, decode(data))
                fault("payload:" + path)
            payload.child("context", True, private=False).close()
            require(manifest(inventory(payload)) == state["files"], "RECOVERY_REQUIRED")
            state["directory_identity"] = payload.identity
            os.fsync(payload.fd)
        fault("payload-flush")
        self.control.put("state.json", state)
        fault("state")
        self.control.unlink("transaction.json")
        fault("transaction-complete")
        self.state = state

    def commit(self, state, files, request, result, event_type=None):
        old_files = self.files() if self.state else {}
        if event_type:
            optional = event_type == "observation"
            if optional and len(files.get("events.jsonl", b"")) + 8192 > MAX_EVENTS:
                state["optional_loss_count"] = state.get("optional_loss_count", 0) + 1
                result["code"] = "OPTIONAL_SUPPRESSED"
                return self.commit(state, files, request, result)
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
            require(len(files["events.jsonl"]) <= MAX_EVENTS, "ARCHIVE_PENDING")
            state["seq"], state["head"] = event["seq"], event["digest"]
            state.pop("archive", None)
        state["files"] = manifest(files)
        result.update(ok=True, code=result.get("code", "OK"), revision=state["revision"],
                      repo_id=state["repo_id"], issue_id=self.id)
        state["requests"][sha(request["request_id"].encode())] = {
            "digest": sha(canonical(request)), "result": result}
        tx = {"base": self.state["files"] if self.state else None,
              "directory_identity": self.state.get("directory_identity") if self.state else None,
              "state": state, "writes": {p: encode(b) for p, b in files.items() if old_files.get(p) != b}}
        fault("before-intent")
        self.control.put("transaction.json", tx)
        fault("intent")
        self.recover()
        return result


def authorize(state, request, coordinator=False, maintenance=False):
    key = participant_key(request)
    participant = state["participants"].get(key)
    require(participant is not None, "BINDING_MISSING")
    require(request.get("binding_generation") == participant["generation"], "STALE_BINDING")
    require(participant["status"] in ({"attached", "ready", "maintenance"} if maintenance else
                                      {"attached", "ready"}), "STALE_BINDING")
    require(not coordinator or state["coordinator"] == key, "NOT_OWNER")
    return key, participant


def expected(state, request):
    require(request.get("expected_revision") == state["revision"], "REVISION_CONFLICT")


def evidence(value):
    require(isinstance(value, list) and 0 < len(value) <= 16, "EVIDENCE_REQUIRED")
    for item in value:
        require(isinstance(item, dict) and set(item) == {"id", "locator", "sha256"}, "EVIDENCE_REQUIRED")
        token(item["id"])
        require(isinstance(item["locator"], str) and 0 < len(item["locator"]) <= 2048,
                "EVIDENCE_REQUIRED")
        require(DIGEST.fullmatch(item["sha256"]), "EVIDENCE_REQUIRED")
    return value


def validate_packet(packet):
    require(isinstance(packet, list) and len(packet) <= 64, "SCOPE_MISSING")
    identifiers = set()
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
    require(participant.get("packet") is not None, "SCOPE_MISSING")
    refs = []
    for ref in participant["packet"]:
        data = None
        try:
            if Path(ref["locator"]).is_absolute():
                path = Path(ref["locator"])
                with Directory.absolute(path.parent) as parent:
                    data = parent.read(path.name, MAX_FILE)
            else:
                data = files.get(ref["locator"])
        except (OSError, WorkspaceError):
            if ref["required"]:
                raise WorkspaceError("SOURCE_STALE") from None
        valid = data is not None and sha(data) == ref["sha256"]
        require(valid or not ref["required"], "SOURCE_STALE")
        refs.append({**ref, "available": valid})
    return refs


def new_state(store, request):
    key = participant_key(request)
    require(request.get("coordinator") == key, "NOT_OWNER")
    return {"schema_version": 1, "repo_id": store.registration["repo_id"],
            "issue_id": request["issue_id"], "issue_uuid": token(request["issue_uuid"]),
            "revision": 0, "generation": 1, "disposition": "active", "storage": "present",
            "coordinator": key, "owners": {"roadmap.md": key}, "participants": {},
            "files": {}, "seq": 0, "head": None, "requests": {}, "provenance": {}}


def attach(state, request):
    key = participant_key(request)
    require(state["disposition"] not in TERMINAL and state["storage"] == "present", "RECOVERY_REQUIRED")
    participant = state["participants"].get(key)
    if participant:
        initial_create = (request["operation"] == "create" and request.get("binding_generation") is None
                          and participant["generation"] == 1 and key == state["coordinator"])
        require(initial_create or request.get("binding_generation") == participant["generation"], "STALE_BINDING")
        require(participant["status"] in {"attached", "ready", "detached"}, "STALE_BINDING")
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
    return {"schema_version": 1, "repo_id": state["repo_id"], "issue_id": state["issue_id"],
            "issue_uuid": state["issue_uuid"], "revision": state["revision"],
            "disposition": state["disposition"], "outcome": state.get("outcome"),
            "seq": state["seq"], "head": state["head"],
            "files": [{"path": p, "size": len(b), "sha256": sha(b), "scope": "coordinator-archive",
                       "data": encode(b)} for p, b in sorted(files.items())]}


def document(payload, heading):
    return heading + "\n\n```json\n" + canonical(payload).decode() + "\n```\n"


def parse_document(content):
    require(isinstance(content, str) and len(content.encode()) <= DOCUMENT_LIMIT, "SIZE_LIMIT")
    matches = re.findall(r"^```json[ \t]*\n(.*?)\n```[ \t]*$", content, re.S | re.M)
    require(len(matches) == 1, "INTEGRITY_ERROR")
    return strict_json(matches[0])


def export_documents(snapshot):
    raw = canonical(snapshot)
    require(len(raw) <= MAX_ARCHIVE, "ARCHIVE_PENDING")
    digest = sha(raw)
    # Byte chunks, not file splitting, bound even one large file.
    chunks = [raw[i:i + ARCHIVE_CHUNK] for i in range(0, len(raw), ARCHIVE_CHUNK)]
    readable = "\n\n".join("File: " + item["path"] + "\n" + decode(item["data"]).decode("utf-8")
                               for item in snapshot["files"])
    width = max(1, (len(readable) + len(chunks) - 1) // len(chunks))
    parts = []
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
    require(isinstance(item, dict) and set(item) == {"id", "url", "issue", "updatedAt", "content",
                                                  "origin", "request_id"}, "ARCHIVE_PENDING")
    require(item["issue"] == issue_uuid and item["origin"] == "linear_get_document", "ARCHIVE_PENDING")
    for field in ("id", "updatedAt", "request_id"):
        token(item[field])
    require(isinstance(item["url"], str) and item["url"].startswith("https://linear.app/"), "ARCHIVE_PENDING")
    return parse_document(item["content"])


def verify_provider(state, observations):
    require(isinstance(observations, list) and 2 <= len(observations) <= 258, "ARCHIVE_PENDING")
    parsed = [provider_observation(x, state["issue_uuid"]) for x in observations]
    roots = [(o, p) for o, p in zip(observations, parsed) if p.get("kind") == "index"]
    require(len(roots) == 1, "ARCHIVE_PENDING")
    root_observed, root = roots[0]
    require(root.get("schema_version") == 1 and root["snapshot"] == state["export"]["snapshot"], "ARCHIVE_PENDING")
    parts = {o["id"]: (o, p) for o, p in zip(observations, parsed) if p.get("kind") == "part"}
    require(len(parts) == len(observations) - 1 == len(root["parts"]), "ARCHIVE_PENDING")
    raw = b""
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
    require(sha(raw) == root["snapshot"], "INTEGRITY_ERROR")
    snapshot = strict_json(raw)
    require(snapshot["schema_version"] == 1 and snapshot["repo_id"] == state["repo_id"] and
            snapshot["issue_id"] == state["issue_id"] and snapshot["issue_uuid"] == state["issue_uuid"],
            "INTEGRITY_ERROR")
    files = {}
    for item in snapshot["files"]:
        path = payload_path(item["path"], True)
        require(path not in files, "INTEGRITY_ERROR")
        data = decode(item["data"])
        require(len(data) == item["size"] and sha(data) == item["sha256"], "INTEGRITY_ERROR")
        data.decode("utf-8")
        files[path] = data
    require("roadmap.md" in files and "events.jsonl" in files, "INTEGRITY_ERROR")
    require(validate_events(files["events.jsonl"]) == (snapshot["seq"], snapshot["head"]), "INTEGRITY_ERROR")
    receipt = {"snapshot": root["snapshot"], "root": {k: v for k, v in root_observed.items() if k != "content"},
               "parts": root["parts"], "read_at": now(), "observations_digest": sha(canonical(observations))}
    return snapshot, files, receipt


def eligible(state):
    require(state["disposition"] in TERMINAL and state.get("outcome"), "RETAINED")
    require(all(p["status"] in {"detached", "maintenance"} and not p["pending"]
                for p in state["participants"].values()), "RETAINED")
    require(state.get("export") and state.get("archive"), "ARCHIVE_PENDING")


def finish_cleanup(issue):
    control, task = issue.control, issue.store.task
    if not control.exists("cleanup.json"):
        return
    intent = control.json("cleanup.json")
    name = intent["quarantine"]
    if task.exists(issue.id):
        require(not control.exists(name), "RECOVERY_REQUIRED")
        with task.child(issue.id, private=False) as payload:
            require(payload.identity == intent["directory_identity"] and
                    manifest(inventory(payload)) == intent["files"], "UNTRACKED_CHANGE")
        os.rename(issue.id, name, src_dir_fd=task.fd, dst_dir_fd=control.fd)
        os.fsync(task.fd)
        os.fsync(control.fd)
        fault("quarantine")
    if control.exists(name):
        with control.child(name, private=False) as payload:
            require(payload.identity == intent["directory_identity"], "UNSAFE_PATH")
            names = os.listdir(payload.fd)
            require(set(names) <= {"roadmap.md", "events.jsonl", "context"}, "UNSAFE_PATH")
            # Verify the ENTIRE remaining tree before deleting any more of it.
            remaining = {}
            for entry in names:
                if entry == "context":
                    with payload.child("context", private=False) as context:
                        for note in os.listdir(context.fd):
                            remaining["context/" + note] = context.read(note, MAX_FILE)
                else:
                    remaining[entry] = payload.read(entry, MAX_FILE)
            require(all(intent["files"].get(p) == sha(b) for p, b in remaining.items()), "UNTRACKED_CHANGE")
            for path in sorted(remaining):
                if path.startswith("context/"):
                    with payload.child("context", private=False) as context:
                        context.unlink(path[8:])
                else:
                    payload.unlink(path)
                fault("delete:" + path)
            if payload.exists("context"):
                os.rmdir("context", dir_fd=payload.fd)
            os.fsync(payload.fd)
        os.rmdir(name, dir_fd=control.fd)
        os.fsync(control.fd)
    state = intent["state"]
    state["storage"] = "cleaned"
    state["requests"] = {k: v for k, v in state["requests"].items()
                         if k == sha(intent["request_id"].encode())}
    state.pop("index_request", None)
    state.pop("checkpoint", None)
    state.pop("provenance", None)
    if control.exists("export.json"):
        control.unlink("export.json")
    state["tombstone"] = {"snapshot": state["export"]["snapshot"], "at": now(),
                          "generation": state["generation"], "cleanup_request": intent["request_id"]}
    control.put("state.json", state)
    fault("cleanup-state")
    control.unlink("cleanup.json")
    issue.state = state


def operate(store, issue, request):
    operation = request["operation"]
    key = participant_key(request)
    issue.recover()
    finish_cleanup(issue)
    state = copy.deepcopy(issue.state)
    if state:
        require(not request.get("issue_uuid") or request["issue_uuid"] == state["issue_uuid"], "ISSUE_MISMATCH")
        previous = state["requests"].get(sha(request["request_id"].encode()))
        if previous:
            require(previous["digest"] == sha(canonical(request)), "REQUEST_CONFLICT")
            if operation in {"create", "adopt", "attach", "resume", "bind", "restore"} and state["storage"] != "cleaned":
                store.view(issue.id)
                store.save_binding(state, key)
            return previous["result"]
    if operation == "diagnose":
        return {"ok": True, "code": "PRESENT" if state else "ABSENT", "repo_id": store.registration["repo_id"],
                "issue_id": issue.id, "revision": state["revision"] if state else None,
                "storage": state["storage"] if state else "absent"}
    binding = store.binding()
    if operation in {"create", "adopt", "attach", "resume", "bind"}:
        require(not binding or binding["issue_id"] == issue.id, "BINDING_CONFLICT")
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
            require(set(owners) == set(files) - {"events.jsonl"} and owners["roadmap.md"] == key,
                    "NOT_OWNER")
            require(all(DIGEST.fullmatch(v) for v in owners.values()), "NOT_OWNER")
            state["owners"] = owners
            state["adoption"] = {"inventory": manifest(files), "evidence": request["evidence"]}
            state["seq"], state["head"] = validate_events(files.get("events.jsonl", b""))
        else:
            template = Path(__file__).resolve().parents[2] / "core/templates/task-workspace/roadmap.md"
            files = {"roadmap.md": template.read_text().replace("{{issue_id}}", issue.id).encode(),
                     "events.jsonl": b""}
        result = attach(state, request)
        result = issue.commit(state, files, request, result, operation)
        store.view(issue.id)
        store.save_binding(issue.state, key)
        return result
    if operation == "restore":
        require(state["storage"] == "cleaned" and state.get("tombstone"), "RECOVERY_REQUIRED")
        require(key == state["coordinator"], "NOT_OWNER")
        snapshot, files, receipt = verify_provider(state, request.get("observations"))
        require(receipt["snapshot"] == state["tombstone"]["snapshot"] and
                manifest(files) == state["files"], "INTEGRITY_ERROR")
        require(not store.task.exists(issue.id), "RECOVERY_REQUIRED")
        state["history"] = state.get("history", []) + [state["tombstone"]]
        state["storage"], state["disposition"] = "present", "active"
        state["provenance"] = {}
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
        store.save_binding(issue.state, key)
        return result
    files = issue.files()
    if operation in {"create", "attach", "resume", "bind"}:
        result = attach(state, request)
        result = issue.commit(state, files, request, result, "attach")
        store.view(issue.id)
        store.save_binding(issue.state, key)
        return result
    require(binding and binding["issue_id"] == issue.id and binding["binding_generation"] == request.get("binding_generation"),
            "BINDING_MISSING")
    maintenance = operation in {"archive-prepare", "archive-index", "archive-verify", "archive-save-start", "archive-observe-save", "cleanup-plan", "cleanup-commit", "reopen"}
    key, participant = authorize(state, request, maintenance=maintenance)
    result = {}
    event_type = operation
    if operation in {"scope", "update", "checkpoint", "outcome", "transfer-coordinator", "reconcile-participant",
                     "archive-prepare", "reopen"}:
        prepared = (operation == "archive-prepare" and sha((request["request_id"] + ":prepare").encode()) in state["requests"])
        if not prepared:
            expected(state, request)
    if operation not in {"read", "ready", "diagnose", "archive-prepare", "archive-index", "archive-verify",
                         "cleanup-plan", "cleanup-commit", "detach", "reopen", "archive-save-start", "archive-observe-save"}:
        require(state["disposition"] not in TERMINAL, "TERMINAL")
    if operation in {"scope", "checkpoint", "outcome", "transfer-coordinator", "reconcile-participant",
                     "archive-prepare", "archive-index", "archive-verify", "archive-save-start", "archive-observe-save", "cleanup-plan", "cleanup-commit", "reopen"}:
        authorize(state, request, coordinator=True, maintenance=maintenance)
    if operation == "scope":
        target = request["target_participant"]
        require(DIGEST.fullmatch(target), "SCOPE_MISSING")
        packet = validate_packet(request["packet"])
        require(all(ref["reader"] == target for ref in packet), "SCOPE_MISSING")
        state.setdefault("assignments", {})[target] = {"packet": packet}
        if target in state["participants"]:
            state["participants"][target].update(packet=packet, ack=None, status="attached")
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
    elif operation == "checkpoint":
        checkpoint = request["checkpoint"]
        require(set(checkpoint) == {"goal", "constraints", "sources", "progress", "blockers", "handoff", "candidate"},
                "EVIDENCE_REQUIRED")
        require(len(canonical(checkpoint)) <= 16384, "SIZE_LIMIT")
        evidence(checkpoint["sources"])
        state["checkpoint"] = checkpoint
        if request.get("submitted_pr"):
            require(str(request["submitted_pr"]).startswith("https://"), "EVIDENCE_REQUIRED")
            state["disposition"] = "in_review"
            state["submitted_pr"] = request["submitted_pr"]
    elif operation == "outcome":
        disposition = request["disposition"]
        require(disposition in TERMINAL | {"active", "blocked", "in_review"})
        evidence(request.get("evidence"))
        if disposition == "completed":
            require(set(request.get("completion", {})) == {"human_acceptance", "merge", "obligations"}, "EVIDENCE_REQUIRED")
            for refs in request["completion"].values():
                evidence(refs)
        if disposition == "failed":
            require(request.get("abandoned") is True, "EVIDENCE_REQUIRED")
        state["disposition"] = disposition
        state["outcome"] = {"disposition": disposition, "evidence": request["evidence"],
                            "completion": request.get("completion"), "at": now()}
    elif operation == "event":
        require(request.get("event_type") in {"check", "tool-start", "tool-complete", "observation"})
        require(set(request.get("event", {})) <= {"code"}, "INVALID_REQUEST")
        require(request.get("event", {}).get("code", "OK") in {"OK", "FAILED", "UNKNOWN", "INTERRUPTED"})
        event_type = request["event_type"]
    elif operation == "tool-start":
        packet_reads(state, participant, files)
        require(participant["ack"] == sha(canonical(participant["packet"])) and participant["status"] == "ready", "NOT_READY")
        tool = token(request["tool_id"])
        require(tool not in participant["pending"], "REQUEST_CONFLICT")
        participant["pending"][tool] = {"status": "pending"}
    elif operation == "tool-complete":
        tool = token(request["tool_id"])
        require(tool in participant["pending"], "UNKNOWN_OPERATION")
        if request.get("async_handle"):
            participant["pending"][tool] = {"status": "unknown", "handle": token(request["async_handle"])}
        else:
            require(request.get("completed") is True, "UNKNOWN_OPERATION")
            del participant["pending"][tool]
    elif operation in {"detach", "reconcile-participant"}:
        target = key if operation == "detach" else request["target_participant"]
        member = state["participants"][target]
        require(request.get("target_generation", request.get("binding_generation")) == member["generation"], "STALE_BINDING")
        evidence(request.get("evidence"))
        require(not member["pending"], "PENDING_OPERATION")
        member["status"], member["ack"] = "detached", None
    elif operation == "transfer-coordinator":
        target = request["target_participant"]
        require(target in state["participants"] and not participant["pending"], "PENDING_OPERATION")
        evidence(request.get("evidence"))
        state["coordinator"] = target
        state["owners"]["roadmap.md"] = target
    elif operation == "reopen":
        evidence(request.get("evidence"))
        require(state["disposition"] in TERMINAL, "INVALID_REQUEST")
        state["disposition"] = "active"
        participant.update(status="attached", ack=None)
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
            issue.commit(state, files, prepare_request, {}, "archive-prepare")
        state = copy.deepcopy(issue.state)
        files = issue.files()
        snapshot = archive_payload(state, files)
        export = export_documents(snapshot)
        state["export"] = {"snapshot": export["snapshot"], "revision": state["revision"],
                           "files": manifest(files), "request_id": request["request_id"]}
        issue.control.put("export.json", export)
        return issue.commit(state, files, request, export)
    elif operation == "archive-index":
        export = issue.control.json("export.json")
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
    elif operation in {"archive-save-start", "archive-observe-save"}:
        export = issue.control.json("export.json")
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
    """Fence the old participant before publishing the new binding; retry is explicit."""
    old_id, new_id = issue_id(request["issue_id"]), issue_id(request["new_issue_id"])
    require(old_id != new_id, "BINDING_CONFLICT")
    key = participant_key(request)
    evidence(request.get("evidence"))
    with ExitStack() as stack:
        controls = {}
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
        target_request = {**request, "operation": "attach", "issue_id": new_id,
                          "binding_generation": request.get("new_binding_generation")}
        result = attach(new_state_value, target_request)
        member.update(status="detached", ack=None)
        if member != old.state["participants"][key]:
            old.commit(old_state, old_files, {**request, "request_id": request["request_id"] + ":retire"}, {}, "rebind")
        result = new.commit(new_state_value, new_files, target_request, result, "rebind")
        store.view(new_id)
        store.save_binding(new.state, key)
        assignment = key + ".assignment.json"
        if store.bindings.exists(assignment):
            store.bindings.put(assignment, {"issue_id": new_id})
        return result


def collect_candidates(store, request):
    """A new issue considers only explicit maintenance assignments with fresh evidence."""
    candidates = request["cleanup_candidates"]
    require(isinstance(candidates, list) and len(candidates) <= 16)
    results = []
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


def execute(request):
    """Return bounded diagnostics. Never include raw exception or task content in errors."""
    try:
        require(isinstance(request, dict) and len(canonical(request)) <= MAX_REQUEST)
        require(request.get("schema_version") == 1)
        token(request["request_id"])
        operation = request["operation"]
        if operation == "register":
            require("repo_id" not in request, "INVALID_REQUEST")
            return register(request)
        if operation == "diagnose" and "repo_id" not in request:
            root, _, _ = repository(request["worktree"])
            with Directory.absolute(root) as directory:
                if not directory.exists(".task"):
                    return {"ok": True, "code": "REGISTRATION_REQUIRED"}
                with directory.child(".task") as local:
                    if not local.exists(".repository.json"):
                        return {"ok": True, "code": "REGISTRATION_REQUIRED"}
                    reg = local.json(".repository.json")
                    return {"ok": True, "code": "REGISTERED", "repo_id": reg["repo_id"]}
        identifier = issue_id(request["issue_id"])
        token(request["repo_id"])
        participant_key(request)
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
    except PermissionError:
        return {"ok": False, "code": "PERMISSION_REQUIRED", "action": "Grant narrow access to the registered issue/control directories and local binding paths."}
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "code": "RECOVERY_REQUIRED", "action": "Inspect filesystem identity and unfinished transactions; retain all data."}
    except (KeyError, TypeError, ValueError, UnicodeError, AttributeError):
        return {"ok": False, "code": "INVALID_REQUEST", "action": "Use the documented version 1 request fields."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-json", help="One bounded JSON object; bootstrap-safe argument route")
    args = parser.parse_args()
    try:
        raw = args.request_json.encode() if args.request_json is not None else sys.stdin.buffer.read(MAX_REQUEST + 1)
        require(len(raw) <= MAX_REQUEST, "SIZE_LIMIT")
        result = execute(strict_json(raw))
    except WorkspaceError as error:
        result = {"ok": False, "code": error.code}
    print(canonical(result).decode())
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
