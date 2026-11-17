import ctypes
import errno
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from ctypes import wintypes

import pytest

from sensorfusion.storage import read_json, write_json


@contextmanager
def windows_json_handle(path, access):
    if sys.platform != "win32":
        pytest.skip("Windows file sharing boundary")
    path.write_text('{"status": "running"}', encoding="utf-8")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), access, 0x3, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())

    def release():
        nonlocal handle
        if handle is not None:
            if not close(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            handle = None

    try:
        yield path, release
    finally:
        release()


@pytest.fixture
def windows_json_reader(tmp_path):
    # Read/write sharing omits FILE_SHARE_DELETE, as a polling reader can.
    with windows_json_handle(tmp_path / "status.json", 0x80000000) as reader:
        yield reader


@pytest.fixture
def windows_json_replacement(tmp_path):
    # A replacement's delete access conflicts with CRT readers that do not share deletion.
    with windows_json_handle(tmp_path / "status.json", 0x10000) as replacement:
        yield replacement


def test_json_publication_recovers_after_a_windows_reader_closes(windows_json_reader, monkeypatch):
    path, release = windows_json_reader
    replace = os.replace
    blocked: list[int] = []

    def replace_after_reader_closes(source, destination):
        try:
            return replace(source, destination)
        except PermissionError as error:
            native_error = getattr(error, "winerror", None)
            assert isinstance(native_error, int)
            blocked.append(native_error)
            assert read_json(path) == {"status": "running"}
            release()
            raise

    monkeypatch.setattr(os, "replace", replace_after_reader_closes)
    write_json(path, {"status": "completed"})
    assert blocked and set(blocked) <= {5, 32}
    assert read_json(path) == {"status": "completed"}
    assert set(path.parent.iterdir()) == {path}


def test_json_publication_bounds_a_persistent_windows_reader(windows_json_reader):
    path, release = windows_json_reader
    finished = threading.Event()
    errors: list[PermissionError] = []

    def publish():
        try:
            write_json(path, {"status": "completed"})
        except PermissionError as error:
            errors.append(error)
        finally:
            finished.set()

    writer = threading.Thread(target=publish, daemon=True)
    writer.start()
    try:
        assert finished.wait(5), "JSON publication did not bound its sharing-conflict wait"
        assert len(errors) == 1 and getattr(errors[0], "winerror", None) in {5, 32}
        assert read_json(path) == {"status": "running"}
        assert set(path.parent.iterdir()) == {path}
    finally:
        release()
        writer.join(timeout=3)
    assert not writer.is_alive()


def test_json_publication_preserves_a_real_replacement_failure(tmp_path):
    path = tmp_path / "status.json"
    path.mkdir()
    with pytest.raises((PermissionError, IsADirectoryError)):
        write_json(path, {"status": "completed"})
    assert path.is_dir()
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.parametrize("invalid_second", [True, False])
def test_json_publication_owns_its_staged_file_during_overlapping_calls(
    tmp_path, monkeypatch, invalid_second
):
    path = tmp_path / "status.json"
    path.write_text('{"status": "running"}', encoding="utf-8")
    staged = threading.Event()
    publish_first = threading.Event()
    replace = os.replace

    def hold_first_publication(source, destination):
        if not staged.is_set():
            staged.set()
            assert publish_first.wait(3), "First JSON publication was not released"
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", hold_first_publication)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(write_json, path, {"status": "first"})
        try:
            assert staged.wait(3), "First JSON publication did not reach replacement"
            if invalid_second:
                with pytest.raises(TypeError):
                    write_json(path, {"unsupported": object()})
                assert read_json(path) == {"status": "running"}
            else:
                write_json(path, {"status": "second"})
                assert read_json(path) == {"status": "second"}
        finally:
            publish_first.set()
        first.result(timeout=3)
    assert read_json(path) == {"status": "first"}
    assert set(tmp_path.iterdir()) == {path}


def test_json_read_recovers_after_a_windows_replacement_handle_closes(
    windows_json_replacement, monkeypatch
):
    path, release = windows_json_replacement
    read_text = type(path).read_text
    conflicts = []

    def read_after_handle_closes(self, *args, **kwargs):
        try:
            return read_text(self, *args, **kwargs)
        except PermissionError as error:
            conflicts.append((error.errno, getattr(error, "winerror", None)))
            release()
            raise

    monkeypatch.setattr(type(path), "read_text", read_after_handle_closes)
    assert read_json(path) == {"status": "running"}
    assert conflicts == [(errno.EACCES, None)]


def test_json_read_bounds_a_persistent_windows_handle(windows_json_replacement):
    path, release = windows_json_replacement
    finished = threading.Event()
    errors: list[PermissionError] = []

    def read():
        try:
            read_json(path)
        except PermissionError as error:
            errors.append(error)
        finally:
            finished.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        assert finished.wait(5), "JSON read did not bound its sharing-conflict wait"
        assert len(errors) == 1
        assert errors[0].errno == errno.EACCES
        assert getattr(errors[0], "winerror", None) is None
    finally:
        release()
        reader.join(timeout=3)
    assert not reader.is_alive()
    assert read_json(path) == {"status": "running"}


@pytest.mark.parametrize(
    ("content", "error"),
    [(b"{broken", json.JSONDecodeError), (b"\xff", UnicodeDecodeError), (None, FileNotFoundError)],
)
def test_json_read_preserves_invalid_and_missing_input_errors(tmp_path, content, error):
    path = tmp_path / "status.json"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(error):
        read_json(path)


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(errno.EPERM, "operation not permitted"),
        PermissionError(errno.EACCES, "native access denied", "status.json", 5),
    ],
)
def test_json_read_does_not_retry_nonsharing_permission_errors(tmp_path, monkeypatch, error):
    path = tmp_path / "status.json"
    attempts = 0

    def fail_read(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise error

    monkeypatch.setattr(type(path), "read_text", fail_read)
    with pytest.raises(PermissionError) as caught:
        read_json(path)
    assert caught.value is error
    assert attempts == 1
