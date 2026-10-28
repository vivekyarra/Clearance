"""Unit tests for Clearance API building blocks: keys, tokens, zip assembly, episodes, dispatch, storage, marker."""
from __future__ import annotations

import io
import json
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import pytest

from services.api import capture_io, episodes, marker, tasks
from services.api.db import Database, JobRow
from services.api.dispatch import DispatchUnavailable, LocalDispatcher, NebiusDispatcher
from services.api.security import BadKey, TokenError, TokenSigner, resolve_under, safe_id, safe_key
from services.api.store import LocalStore, S3Store


# ---------------------------------------------------------------- keys + tokens
@pytest.mark.parametrize("key", ["captures/cap_1/upload/part_0000.zip", "a", "frames/000123.jpg", "x-y_z.v2.json"])
def test_safe_key_accepts(key):
    assert safe_key(key) == key


@pytest.mark.parametrize("key", ["", "/a", "a/../b", "..", "a//b", "a\\b", "C:/x", "a/./b", ".env", "a/.git/x",
                                 "con", "aux.txt", "a\x00b", "a b", "x" * 600, None, 3])
def test_safe_key_rejects(key):
    with pytest.raises(BadKey):
        safe_key(key)


def test_resolve_under_stays_inside(tmp_path):
    assert resolve_under(tmp_path, "a/b.txt") == (tmp_path / "a" / "b.txt").resolve()
    with pytest.raises(BadKey):
        resolve_under(tmp_path, "../x")


def test_safe_id():
    assert safe_id("site_ab-12") == "site_ab-12"
    for bad in ("a.b", "a/b", "", "-x" * 100):
        with pytest.raises(BadKey):
            safe_id(bad)


def test_token_roundtrip_tamper_expiry():
    s = TokenSigner(b"k1")
    tok = s.sign({"k": "captures/c/x", "m": 5}, 60)
    p = s.verify(tok)
    assert p["k"] == "captures/c/x" and p["exp"] > time.time()
    body, mac = tok.split(".")
    for bad in (body[:-1] + ("A" if body[-1] != "A" else "B") + "." + mac, body + "." + mac[:-2], "a.b.c", "", "x" * 5000):
        with pytest.raises(TokenError):
            s.verify(bad)
    with pytest.raises(TokenError):
        TokenSigner(b"k2").verify(tok)
    with pytest.raises(TokenError, match="expired"):
        s.verify(s.sign({"k": "a"}, -1))
    with pytest.raises(ValueError):
        TokenSigner(b"")


# ---------------------------------------------------------------- capture assembly
def _zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


def test_assemble_independent_parts_and_skips_metadata(tmp_path):
    p0, p1 = tmp_path / "p0.zip", tmp_path / "p1.zip"
    p0.write_bytes(_zip({"cap/poses.jsonl": b"{}\n", "__MACOSX/cap/._x": b"x", "cap/.DS_Store": b"x"}))
    p1.write_bytes(_zip({"cap/frames/000000.jpg": b"jpg"}))
    dest = tmp_path / "out"
    names = capture_io.assemble([p0, p1], dest, chunking="independent", max_bytes=10_000, work_dir=tmp_path / "w")
    assert sorted(names) == ["cap/frames/000000.jpg", "cap/poses.jsonl"]
    assert capture_io.find_capture_root(dest) == dest / "cap"


def test_assemble_split_parts(tmp_path):
    blob = _zip({"poses.jsonl": b"{}\n" * 100, "frames/a.jpg": b"j" * 1000})
    parts = []
    for i in range(4):
        p = tmp_path / f"part{i}"
        p.write_bytes(blob[i * len(blob) // 4:(i + 1) * len(blob) // 4])
        parts.append(p)
    dest = tmp_path / "out"
    capture_io.assemble(parts, dest, chunking="split", max_bytes=10_000, work_dir=tmp_path / "w")
    assert (dest / "frames" / "a.jpg").read_bytes() == b"j" * 1000
    assert not (tmp_path / "w" / "joined.zip").exists()


@pytest.mark.parametrize("name", ["../x", "/abs", "a/../../x", "a/b/../../../x"])
def test_assemble_rejects_unsafe_entries(tmp_path, name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo(name), b"x")
    p = tmp_path / "p.zip"
    p.write_bytes(buf.getvalue())
    with pytest.raises(capture_io.CaptureError):
        capture_io.assemble([p], tmp_path / "out", chunking="independent", max_bytes=100, work_dir=tmp_path / "w")
    assert not (tmp_path / "x").exists()


def test_assemble_size_budget(tmp_path):
    p = tmp_path / "p.zip"
    p.write_bytes(_zip({"big.bin": b"\0" * 5000}))  # compresses well: guard uses declared sizes
    with pytest.raises(capture_io.CaptureError, match="maximum"):
        capture_io.assemble([p], tmp_path / "out", chunking="independent", max_bytes=1000, work_dir=tmp_path / "w")


def test_hash_tree_is_content_addressed(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        (d / "f").mkdir(parents=True)
        (d / "f" / "1.txt").write_text("one")
        (d / "capture.json").write_text(json.dumps({"capture_id": str(d)}))
    assert tasks.hash_tree(a) != tasks.hash_tree(b)
    assert tasks.hash_tree(a, exclude=("capture.json",)) == tasks.hash_tree(b, exclude=("capture.json",))
    assert tasks.hash_tree(a, extra={"x": 1}) != tasks.hash_tree(a, extra={"x": 2})


# ---------------------------------------------------------------- episodes
def _write_lines(p: Path, rows):
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_episode_layouts(tmp_path):
    p = tmp_path / "a.jsonl"
    _write_lines(p, [{"episode": e, "t": k / 10, "events": [{"type": "stop"}] if k == 1 else []}
                     for e in (1, 0) for k in range(3)])
    idx = episodes.EpisodeIndex(p)
    assert len(idx) == 2 and idx.layout == "frames_with_index"
    ep0 = idx.episode(0)
    assert all(f["episode"] == 0 for f in ep0["frames"]) and ep0["events"] == [{"type": "stop", "t": 0.1}]

    q = tmp_path / "b.jsonl"
    _write_lines(q, [{"seed": 1, "frames": [{"t": 0}, {"t": 0.1}]}, {"seed": 2, "frames": [{"t": 0}]}])
    idx = episodes.EpisodeIndex(q)
    assert idx.layout == "episode_per_line" and idx.episode(1)["meta"] == {"seed": 2}

    r = tmp_path / "c.jsonl"
    r.write_text("\n".join(json.dumps({"t": t}) for t in (0, 0.1, 0.2, 0, 0.1, 0)) + "\n\nnot json\n")
    idx = episodes.EpisodeIndex(r)
    assert len(idx) == 3 and idx.episode(1)["n_frames"] == 2
    with pytest.raises(IndexError):
        idx.episode(3)


def test_episode_cache_invalidates_on_change(tmp_path):
    p = tmp_path / "a.jsonl"
    _write_lines(p, [{"t": 0}])
    cache = episodes.EpisodeCache()
    assert len(cache.get(p)) == 1
    time.sleep(0.01)
    _write_lines(p, [{"t": 1}, {"t": 0}])
    assert len(cache.get(p)) == 2


def test_episode_index_large_file(tmp_path):
    p = tmp_path / "big.jsonl"
    with p.open("w") as f:
        for e in range(200):
            for k in range(50):
                f.write(json.dumps({"ep": e, "t": k / 10, "robot": [k, 0, 0, 0.5], "peds": []}) + "\n")
    t0 = time.monotonic()
    idx = episodes.EpisodeIndex(p)
    assert len(idx) == 200 and idx.episode(199)["n_frames"] == 50
    assert time.monotonic() - t0 < 5


# ---------------------------------------------------------------- dispatch
def _job(x, delay=0.0, fail=False):
    time.sleep(delay)
    if fail:
        raise ValueError("requested failure")
    return {"y": x * 2}


@pytest.fixture
def db(tmp_path):
    d = Database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    d.create_all()
    yield d
    d.dispose()


def test_local_dispatcher_idempotent_and_reruns_failures(db, monkeypatch):
    import sys
    import types

    mod = types.ModuleType("clearance_dispatch_target")
    mod.job = _job
    monkeypatch.setitem(sys.modules, "clearance_dispatch_target", mod)
    disp = LocalDispatcher(db, max_workers=2)
    done = []
    jid, new = disp.submit("k", "clearance_dispatch_target:job", {"x": 2}, input_hash="h1",
                           on_success=lambda j, r: done.append(r))
    assert new
    assert disp.wait(jid, 5)["status"] == "succeeded" and done == [{"y": 4}]
    assert disp.get(jid)["result"] == {"y": 4} and disp.get(jid)["duration_s"] is not None
    assert disp.submit("k", "clearance_dispatch_target:job", {"x": 2}, input_hash="h1") == (jid, False)

    errs = []
    jid2, _ = disp.submit("k", "clearance_dispatch_target:job", {"x": 1, "fail": True}, input_hash="h2",
                          on_error=lambda j, e: errs.append(str(e)))
    st = disp.wait(jid2, 5)
    assert st["status"] == "failed" and "requested failure" in st["error"] and errs == ["requested failure"]
    jid3, new3 = disp.submit("k", "clearance_dispatch_target:job", {"x": 1}, input_hash="h2")
    assert jid3 == jid2 and new3 and disp.wait(jid3, 5)["status"] == "succeeded"
    assert disp.get(jid3)["attempts"] == 2
    disp.shutdown(wait=True)


def test_local_dispatcher_finalizer_failure_fails_job(db, monkeypatch):
    import sys
    import types

    mod = types.ModuleType("clearance_dispatch_target2")
    mod.job = _job
    monkeypatch.setitem(sys.modules, "clearance_dispatch_target2", mod)
    disp = LocalDispatcher(db, max_workers=1)

    def finalizer_raises(j, r):
        raise RuntimeError("finalize failed")

    jid, _ = disp.submit("k", "clearance_dispatch_target2:job", {"x": 1}, input_hash="h", on_success=finalizer_raises)
    assert disp.wait(jid, 5)["status"] == "failed"
    disp.shutdown(wait=True)


def test_local_dispatcher_parallel_and_concurrent_claims(db, monkeypatch):
    import sys
    import types

    barrier = threading.Barrier(3)
    mod = types.ModuleType("clearance_dispatch_target3")
    mod.job = lambda i: (barrier.wait(timeout=5), {"i": i})[1]
    monkeypatch.setitem(sys.modules, "clearance_dispatch_target3", mod)
    disp = LocalDispatcher(db, max_workers=3)
    ids = [disp.submit("p", "clearance_dispatch_target3:job", {"i": i}, input_hash=f"h{i}")[0] for i in range(3)]
    assert all(disp.wait(j, 10)["status"] == "succeeded" for j in ids)
    # many threads claiming the same input -> exactly one runs
    mod.job = lambda i: {"i": i}
    outs = []
    ts = [threading.Thread(target=lambda: outs.append(
        disp.submit("q", "clearance_dispatch_target3:job", {"i": 0}, input_hash="same")[1])) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert outs.count(True) == 1
    disp.shutdown(wait=True)


def test_recover_interrupted(db):
    with db.session() as s:
        s.add(JobRow(id="job_x", kind="recon", input_hash="h", status="running", backend="local"))
    disp = LocalDispatcher(db, max_workers=1)
    assert disp.recover_interrupted() == 1
    assert disp.get("job_x")["status"] == "failed"
    disp.shutdown()


def test_process_mode_runs_top_level_callable(db):
    disp = LocalDispatcher(db, max_workers=1, mode="process")
    jid, _ = disp.submit("hash", "services.api.tasks:hash_json", {"obj": {"a": 1}}, input_hash="p1")
    st = disp.wait(jid, 60)
    assert st["status"] == "succeeded" and st["result"] == tasks.hash_json({"a": 1})
    disp.shutdown(wait=True)


def test_nebius_dispatcher_builds_submission(db, tmp_path):
    jobs = tmp_path / "infra" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "recon.yaml").write_text(
        "image: registry.example/clearance-jobs:1\nenv: {CLEARANCE_LLM_MODE: offline}\n"
        "submit_command: [nebius-cli, submit, --name, '{job_id}', --image, '{image}']\n"
        "status_command: [nebius-cli, status, '{job_id}']\n")
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        out = json.dumps({"status": "succeeded"}) if cmd[1] == "status" else "ok"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    d = NebiusDispatcher(db, tmp_path / "infra", submit_enabled=False, runner=runner)
    sub = d.build_submission("job_recon_abc", "recon", "services.api.tasks:run_recon", {"site_id": "s"})
    assert sub["command"] == ["nebius-cli", "submit", "--name", "job_recon_abc", "--image",
                              "registry.example/clearance-jobs:1"]
    assert json.loads(sub["env"]["CLEARANCE_JOB_SPEC"])["kwargs"] == {"site_id": "s"}
    assert sub["env"]["CLEARANCE_LLM_MODE"] == "offline"
    with pytest.raises(DispatchUnavailable, match="CLEARANCE_NEBIUS_SUBMIT"):
        d.submit("recon", "services.api.tasks:run_recon", {}, input_hash="h")
    with pytest.raises(DispatchUnavailable, match="no Nebius job definition"):
        d.definition("analysis")

    d.submit_enabled = True
    got = []
    jid, new = d.submit("recon", "services.api.tasks:run_recon", {}, input_hash="h",
                        on_success=lambda j, r: got.append(r))
    assert new and d.get(jid)["status"] == "submitted"
    st = d.refresh(jid, load_result=lambda j: {"twin": "ok"})
    assert st["status"] == "succeeded" and got == [{"twin": "ok"}]


# ---------------------------------------------------------------- storage
def test_local_store_basics(tmp_path):
    st = LocalStore(tmp_path, TokenSigner(b"k"))
    st.write_json("a/b.json", {"x": 1})
    assert st.read_json("a/b.json") == {"x": 1} and st.exists("a/b.json")
    assert st.list_keys("a") == ["a/b.json"]
    st.write_bytes("src/t/1.bin", b"1")
    st.write_bytes("src/t/2/3.bin", b"3")
    assert st.move_tree("src", "dst") == ["dst/t/1.bin", "dst/t/2/3.bin"]
    assert not (tmp_path / "src").exists()
    with pytest.raises(BadKey):
        st.write_bytes("../x", b"")
    url = st.presign_put("captures/c/x", api_base="http://h:8000/", ttl_s=60, capture_id="c", max_bytes=9)
    assert url["url"].startswith("http://h:8000/upload/") and url["method"] == "PUT"


def test_read_json_tolerates_partial_writes(tmp_path):
    st = LocalStore(tmp_path, TokenSigner(b"k"))
    p = st.path("x.json")
    p.write_text('{"a": ')

    def finish():
        time.sleep(0.03)
        p.write_text('{"a": 1}')

    t = threading.Thread(target=finish)
    t.start()
    assert st.read_json("x.json", retries=10) == {"a": 1}
    t.join()


class _S3ClientDouble:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803 - boto3 signature
        return f"https://bucket.example/{Params['Key']}?X-Amz-Expires={ExpiresIn}&op={op}"

    def generate_presigned_post(self, Bucket, Key, Fields=None, Conditions=None, ExpiresIn=3600):  # noqa: N803
        self.last_post = {"Bucket": Bucket, "Key": Key, "Conditions": Conditions, "ExpiresIn": ExpiresIn}
        return {"url": f"https://bucket.example/{Bucket}", "fields": {"key": Key, "policy": "p", "x-amz-signature": "s"}}

    def upload_file(self, filename, bucket, key):
        self.objects[key] = Path(filename).read_bytes()

    def download_file(self, bucket, key, filename):
        if key not in self.objects:
            raise KeyError(key)
        Path(filename).write_bytes(self.objects[key])

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise KeyError(Key)
        return {}

    def list_objects_v2(self, Bucket, Prefix, **kw):  # noqa: N803
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)], "IsTruncated": False}


def test_s3_store_mirrors_and_presigns(tmp_path):
    cl = _S3ClientDouble()
    st = S3Store(tmp_path / "cache", TokenSigner(b"k"), bucket="b", client=cl, prefix="clearance")
    st.write_json("sites/s/site.json", {"a": 1})
    assert json.loads(cl.objects["clearance/sites/s/site.json"]) == {"a": 1}
    u = st.presign_put("captures/c/upload/part_0000.zip", api_base="x", ttl_s=30, capture_id="c", max_bytes=1)
    # presigned POST: the signed policy caps the object size (a presigned PUT cannot)
    assert u["method"] == "POST" and u["url"] == "https://bucket.example/b" and u["file_field"] == "file"
    assert u["fields"]["key"] == "clearance/captures/c/upload/part_0000.zip"
    assert cl.last_post["Conditions"] == [["content-length-range", 0, 1]] and cl.last_post["ExpiresIn"] == 30
    cl.objects["clearance/captures/c/upload/part_0000.zip"] = b"zipdata"
    assert st.exists("captures/c/upload/part_0000.zip")
    assert st.ensure_local("captures/c/upload/part_0000.zip").read_bytes() == b"zipdata"
    assert "captures/c/upload/part_0000.zip" in st.list_keys("captures/c")
    with pytest.raises(FileNotFoundError):
        st.ensure_local("captures/c/none.bin")


def test_s3_store_refreshes_keys_rewritten_by_remote_jobs(tmp_path):
    cl = _S3ClientDouble()
    st = S3Store(tmp_path / "cache", TokenSigner(b"k"), bucket="b", client=cl)
    cl.objects["analyses/a1/analysis.json"] = b'{"status": "running"}'
    cl.objects["sites/s/twin_v1/occ.png"] = b"png1"
    assert st.read_json("analyses/a1/analysis.json") == {"status": "running"}
    assert st.read_bytes("sites/s/twin_v1/occ.png") == b"png1"
    cl.objects["analyses/a1/analysis.json"] = b'{"status": "done"}'
    cl.objects["sites/s/twin_v1/occ.png"] = b"png2"  # immutable keys are served from the cache
    assert st.read_json("analyses/a1/analysis.json") == {"status": "done"}
    assert st.read_bytes("sites/s/twin_v1/occ.png") == b"png1"
    del cl.objects["analyses/a1/analysis.json"]  # bucket unreachable -> last cached copy
    assert st.read_json("analyses/a1/analysis.json") == {"status": "done"}


# ---------------------------------------------------------------- marker
def test_tag_cells_have_black_border():
    cells = marker.tag_cells(0)
    assert cells.shape == (8, 8)
    assert cells[0].all() and cells[-1].all() and cells[:, 0].all() and cells[:, -1].all()
    assert not cells[1:-1, 1:-1].all()
    assert not (marker.tag_cells(0) == marker.tag_cells(1)).all()


def test_marker_pdf_structure():
    pdf = marker.marker_pdf("site_x", "Café (Main) \\ hall", size_m=0.16)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert b"/MediaBox [0 0 595.2756 841.8898]" in pdf
    xref = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
    assert pdf[xref:xref + 4] == b"xref"
    for bad in (0.01, 0.30):
        with pytest.raises(ValueError):
            marker.marker_pdf("s", "n", size_m=bad)
