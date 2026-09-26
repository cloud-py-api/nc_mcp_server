"""Unit tests for pure helpers in tools.files."""

import os
from pathlib import Path

import pytest

from nc_mcp_server.tools.files import (
    _build_search_xml,
    _like_literal,
    _open_no_follow,
    _resolve_content_type,
    _resolve_local_upload_path,
)


class TestResolveContentType:
    def test_explicit_content_type_kept(self) -> None:
        assert _resolve_content_type("photo.png", "custom/type") == "custom/type"

    def test_explicit_content_type_kept_for_unknown_extension(self) -> None:
        assert _resolve_content_type("blob.weirdext", "application/x-custom") == "application/x-custom"

    def test_png_extension_inferred(self) -> None:
        assert _resolve_content_type("photo.png", "") == "image/png"

    def test_pdf_extension_inferred(self) -> None:
        assert _resolve_content_type("doc.pdf", "") == "application/pdf"

    def test_jpeg_extension_inferred(self) -> None:
        assert _resolve_content_type("photo.jpeg", "") == "image/jpeg"

    def test_unknown_extension_falls_back(self) -> None:
        assert _resolve_content_type("blob.weirdext", "") == "application/octet-stream"

    def test_no_extension_falls_back(self) -> None:
        assert _resolve_content_type("noext", "") == "application/octet-stream"

    def test_nested_path_inference(self) -> None:
        assert _resolve_content_type("deep/sub/path/photo.png", "") == "image/png"

    def test_whitespace_only_content_type_falls_back_to_inference(self) -> None:
        assert _resolve_content_type("photo.png", "   ") == "image/png"
        assert _resolve_content_type("photo.png", "\t\n") == "image/png"

    def test_whitespace_only_content_type_falls_back_to_octet_stream(self) -> None:
        assert _resolve_content_type("blob.weirdext", "   ") == "application/octet-stream"

    def test_explicit_content_type_trimmed(self) -> None:
        assert _resolve_content_type("photo.png", "  image/png  ") == "image/png"


class TestResolveLocalUploadPath:
    def test_unset_upload_root_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "hi.txt"
        f.write_bytes(b"hi")
        with pytest.raises(ValueError, match="not configured"):
            _resolve_local_upload_path(str(f), "")

    def test_empty_local_path_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            _resolve_local_upload_path("", str(tmp_path))

    def test_whitespace_only_local_path_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            _resolve_local_upload_path("   ", str(tmp_path))

    def test_file_inside_root_accepted(self, tmp_path: Path) -> None:
        f = tmp_path / "hi.txt"
        f.write_bytes(b"hi")
        result = _resolve_local_upload_path(str(f), str(tmp_path))
        assert result == f.resolve()

    def test_file_in_subdirectory_accepted(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub" / "nested"
        sub.mkdir(parents=True)
        f = sub / "deep.txt"
        f.write_bytes(b"hi")
        result = _resolve_local_upload_path(str(f), str(tmp_path))
        assert result == f.resolve()

    def test_nonexistent_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            _resolve_local_upload_path(str(tmp_path / "nope.txt"), str(tmp_path))

    def test_directory_rejected(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        with pytest.raises(ValueError, match="not a regular file"):
            _resolve_local_upload_path(str(sub), str(tmp_path))

    def test_file_outside_root_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "allowed"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"secret")
        with pytest.raises(ValueError, match="outside the configured upload root"):
            _resolve_local_upload_path(str(outside), str(root))

    def test_parent_traversal_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "allowed"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"secret")
        traversal = root / ".." / "outside.txt"
        with pytest.raises(ValueError, match="outside the configured upload root"):
            _resolve_local_upload_path(str(traversal), str(root))

    def test_symlink_pointing_outside_root_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "allowed"
        root.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_bytes(b"sshh")
        link = root / "link.txt"
        link.symlink_to(outside)
        with pytest.raises(ValueError, match="outside the configured upload root"):
            _resolve_local_upload_path(str(link), str(root))

    def test_symlink_pointing_inside_root_accepted(self, tmp_path: Path) -> None:
        root = tmp_path / "allowed"
        root.mkdir()
        target = root / "target.txt"
        target.write_bytes(b"ok")
        link = root / "link.txt"
        link.symlink_to(target)
        result = _resolve_local_upload_path(str(link), str(root))
        assert result == target.resolve()

    def test_upload_root_with_symlink_ancestor(self, tmp_path: Path) -> None:
        real_root = tmp_path / "real"
        real_root.mkdir()
        link_root = tmp_path / "link"
        link_root.symlink_to(real_root)
        f = real_root / "hi.txt"
        f.write_bytes(b"hi")
        result = _resolve_local_upload_path(str(f), str(link_root))
        assert result == f.resolve()

    def test_relative_path_resolved_against_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = tmp_path / "hi.txt"
        f.write_bytes(b"hi")
        monkeypatch.chdir(tmp_path)
        result = _resolve_local_upload_path("hi.txt", str(tmp_path))
        assert result == f.resolve()

    def test_fifo_rejected(self, tmp_path: Path) -> None:
        fifo = tmp_path / "pipe"
        os.mkfifo(fifo)
        with pytest.raises(ValueError, match="not a regular file"):
            _resolve_local_upload_path(str(fifo), str(tmp_path))

    def test_tilde_expansion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        f = tmp_path / "hi.txt"
        f.write_bytes(b"hi")
        result = _resolve_local_upload_path("~/hi.txt", str(tmp_path))
        assert result == f.resolve()


class TestOpenNoFollow:
    """O_NOFOLLOW guard — defense-in-depth against TOCTOU symlink swap."""

    def test_regular_file_opens(self, tmp_path: Path) -> None:
        f = tmp_path / "real.bin"
        payload = b"hello"
        f.write_bytes(payload)
        fh = _open_no_follow(f)
        try:
            assert fh.read() == payload
        finally:
            fh.close()

    def test_symlink_to_file_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "target.txt"
        target.write_bytes(b"secret")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        with pytest.raises(ValueError, match="swapped after validation"):
            _open_no_follow(link)

    def test_broken_symlink_rejected(self, tmp_path: Path) -> None:
        link = tmp_path / "dangling.txt"
        link.symlink_to(tmp_path / "does-not-exist")
        with pytest.raises(ValueError, match="swapped after validation"):
            _open_no_follow(link)

    def test_missing_file_raises_oserror(self, tmp_path: Path) -> None:
        # Not ELOOP — should propagate as-is, not the swap message
        with pytest.raises(FileNotFoundError):
            _open_no_follow(tmp_path / "nope.txt")


class TestSearchLiteral:
    @pytest.mark.parametrize(
        ("text", "escaped"),
        [("report", "report"), ("a_b", "a\\_b"), ("50%off", "50\\%off"), ("x\\y", "x\\\\y"), ("%_", "\\%\\_")],
    )
    def test_wildcards_are_escaped(self, text: str, escaped: str) -> None:
        assert _like_literal(text) == escaped

    def test_query_is_escaped_and_mimetype_keeps_its_wildcard(self) -> None:
        body = _build_search_xml("admin", "a_b<", "/", 20, 0, "image")
        assert "<d:literal>%a\\_b&lt;%</d:literal>" in body
        assert "<d:literal>image/%</d:literal>" in body
