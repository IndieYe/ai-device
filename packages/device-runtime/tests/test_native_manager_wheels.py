import runpy
from pathlib import Path

import pytest

select = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts/prepare-native-manager-wheels.py")
)["select_wheels"]


def lock(*filenames):
    return {
        "package": [
            {
                "name": "fixture",
                "version": "1",
                "wheels": [
                    {
                        "url": "https://example.invalid/" + name,
                        "hash": "sha256:" + "a" * 64,
                        "size": 1,
                    }
                    for name in filenames
                ],
            }
        ]
    }


def test_selector_excludes_free_threaded_prefers_compatible_abi3():
    data = lock(
        "fixture-1-cp313-cp313t-manylinux2014_x86_64.whl",
        "fixture-1-cp36-abi3-manylinux2014_x86_64.whl",
    )
    assert select(data, "fixture==1")[0]["name"].startswith("fixture-1-cp36-abi3")


@pytest.mark.parametrize(
    "filename",
    [
        "fixture-1-cp313-cp313t-manylinux2014_x86_64.whl",
        "fixture-1-cp314-abi3-manylinux2014_x86_64.whl",
        "fixture-1-cp313-cp313-manylinux2014_aarch64.whl",
    ],
)
def test_incompatible_wheel_not_selected(filename):
    with pytest.raises(ValueError, match="compatible"):
        select(lock(filename), "fixture==1")


def test_exact_requirement_required():
    with pytest.raises(ValueError, match="exact"):
        select(lock(), "fixture>=1")
