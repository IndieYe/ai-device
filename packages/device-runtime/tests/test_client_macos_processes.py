import pytest

from device_runtime.client_macos_processes import (
    ObservedProcessTree,
    ProcessEvidenceError,
    ProcessIdentity,
    ProcessObservation,
)


def process(pid, parent, started=100):
    return ProcessObservation(ProcessIdentity(pid, started), parent)


def test_remembers_reparented_descendants_after_main_exits():
    root = process(10, 1)
    child = process(20, 10)
    grandchild = process(30, 20)
    tree = ObservedProcessTree(root.identity)
    assert tree.observe([grandchild, child, root], complete=True) == {
        root.identity,
        child.identity,
        grandchild.identity,
    }
    assert tree.observe([process(30, 1)], complete=True) == {grandchild.identity}
    assert tree.observe([], complete=True) == set()


def test_pid_reuse_does_not_capture_unrelated_tree():
    root = process(10, 1)
    tree = ObservedProcessTree(root.identity)
    tree.observe([root, process(20, 10)], complete=True)
    assert tree.observe([process(10, 1, 200), process(40, 10, 201)], complete=True) == set()
    assert tree.observe([process(20, 1)], complete=True) == {ProcessIdentity(20, 100)}


def test_new_children_of_reparented_known_child_are_retained():
    tree = ObservedProcessTree(ProcessIdentity(10, 100))
    tree.observe([process(10, 1), process(20, 10)], complete=True)
    assert tree.observe([process(20, 1), process(30, 20, 200)], complete=True) == {
        ProcessIdentity(20, 100),
        ProcessIdentity(30, 200),
    }


@pytest.mark.parametrize("complete", [False, None, 1, "true"])
def test_incomplete_snapshot_never_means_stopped(complete):
    tree = ObservedProcessTree(ProcessIdentity(10, 100))
    with pytest.raises(ProcessEvidenceError, match="incomplete"):
        tree.observe([], complete=complete)


def test_duplicate_pid_rejected_without_changing_evidence():
    tree = ObservedProcessTree(ProcessIdentity(10, 100))
    with pytest.raises(ProcessEvidenceError, match="duplicate"):
        tree.observe([process(10, 1), process(10, 1, 200)], complete=True)
    assert tree.observed == {ProcessIdentity(10, 100)}


@pytest.mark.parametrize(
    "pid,started", [(0, 1), (True, 1), (10, 0), (10, float("nan")), (10, float("inf")), (10, "100")]
)
def test_bad_identity_rejected(pid, started):
    with pytest.raises(ProcessEvidenceError):
        ProcessIdentity(pid, started)


def test_unseen_reparented_process_is_not_claimed_as_proven_absent():
    # Documents the limitation: callers still need a start/lifetime barrier.
    tree = ObservedProcessTree(ProcessIdentity(10, 100))
    assert tree.observe([process(90, 1)], complete=True) == set()
    assert not hasattr(tree, "safe_to_replace")
