"""A migration receipt has to notice loss and silent regeneration."""

from __future__ import annotations

from source_inventory import compare, scan


def make_tree(root, files):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def test_an_identical_copy_is_intact(tmp_path):
    src = make_tree(tmp_path / "a", {"x.py": "1", "pkg/y.py": "2"})
    dst = make_tree(tmp_path / "b", {"x.py": "1", "pkg/y.py": "2"})
    result = compare(scan(src), scan(dst))
    assert result.clean
    assert scan(src)["tree_digest"] == scan(dst)["tree_digest"]


def test_a_lost_file_is_reported_and_fails(tmp_path):
    src = make_tree(tmp_path / "a", {"x.py": "1", "pkg/y.py": "2"})
    dst = make_tree(tmp_path / "b", {"x.py": "1"})
    result = compare(scan(src), scan(dst))
    assert result.missing == ("pkg/y.py",)
    assert not result.clean


def test_a_regenerated_file_is_changed_not_intact(tmp_path):
    src = make_tree(tmp_path / "a", {"x.py": "original"})
    dst = make_tree(tmp_path / "b", {"x.py": "regenerated"})
    result = compare(scan(src), scan(dst))
    assert result.changed == ("x.py",)
    assert not result.clean


def test_extra_files_in_the_destination_do_not_fail_the_migration(tmp_path):
    src = make_tree(tmp_path / "a", {"x.py": "1"})
    dst = make_tree(tmp_path / "b", {"x.py": "1", "README.md": "new"})
    result = compare(scan(src), scan(dst))
    assert result.added == ("README.md",)
    assert result.clean


def test_noise_directories_are_not_inventoried(tmp_path):
    src = make_tree(tmp_path / "a", {"x.py": "1", "node_modules/dep/index.js": "junk"})
    assert set(scan(src)["files"]) == {"x.py"}
