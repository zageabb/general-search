from importlib.metadata import version

from ddgs import DDGS


def test_ddgs_version_is_modern_enough():
    installed = version("ddgs")
    major, minor, *_ = [int(x) for x in installed.split(".")]
    assert (major, minor) >= (9, 14)


def test_primp_version_is_pinned():
    assert version("primp") == "2.0.1"


def test_ddgs_object_can_be_created():
    client = DDGS(timeout=5)
    assert client is not None


def test_expected_backends_still_exist():
    backends = {"duckduckgo", "mojeek", "startpage", "yahoo"}
    assert "duckduckgo" in backends
    assert "mojeek" in backends
    assert "startpage" in backends
    assert "yahoo" in backends
