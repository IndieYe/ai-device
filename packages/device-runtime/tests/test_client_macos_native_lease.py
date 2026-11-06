import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin peer identity")


@pytest.fixture
def binary(tmp_path):
    source = Path(__file__).parents[1] / "native/client-macos-lease-request.m"
    harness = tmp_path / "lease.m"
    harness.write_text(
        f'#include "{source}"\n'
        "int main(int argc,char **argv) { @autoreleasepool {\n"
        " if(argc!=2)return 64;int fd=socket(AF_UNIX,SOCK_STREAM,0);\n"
        " struct sockaddr_un address={0};address.sun_family=AF_UNIX;\n"
        " if(strlen(argv[1])>=sizeof(address.sun_path))return 64;\n"
        " strcpy(address.sun_path,argv[1]);\n"
        " if(connect(fd,(struct sockaddr *)&address,sizeof(address)))return 70;\n"
        " struct lease_original original;\n"
        ' int result=lease_request_connected(fd,getuid()+(getenv("WRONG_UID")!=NULL),lease_clock()+0.5,&original);\n'
        ' if(getenv("WAIT_ORIGINAL") && result==LEASE_ACCEPTED) {\n'
        " if(lease_wait_original_exit(&original,lease_clock()+2)!=1)return 71; }\n"
        ' close(fd);printf("%d\\n",result);return 0;}}\n'
    )
    binary = tmp_path / "lease"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fobjc-arc",
            "-framework",
            "Foundation",
            str(harness),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    return binary


@pytest.mark.parametrize(
    "case",
    [
        "accepted",
        "busy",
        "wrong-type",
        "bad-challenge",
        "lost-ack",
        "oversize",
        "wrong-uid",
        "timeout",
    ],
)
def test_native_same_connection_exit_never_reconnects(binary, case):
    events = []
    failures = []
    with tempfile.TemporaryDirectory(prefix="d-nlease-", dir="/tmp") as directory:
        path = Path(directory) / "s"
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path))
            listener.listen(2)
            listener.settimeout(2)

            def serve():
                try:
                    connection, _ = listener.accept()
                    events.append("connected")
                    with connection:
                        connection.settimeout(2)
                        stream = connection.makefile("rb", buffering=0)
                        try:
                            if case == "wrong-uid":
                                assert stream.read(1) == b""
                                return
                            assert json.loads(stream.readline()) == {"action": "acquire"}
                            if case == "timeout":
                                # Wait for bounded client deadline to close, not a service stop.
                                assert stream.read(1) == b""
                                return
                            if case == "oversize":
                                connection.sendall(b" " * 4096 + b"\n")
                                return
                            acquired = (
                                False if case == "busy" else (1 if case == "wrong-type" else True)
                            )
                            connection.sendall(json.dumps({"acquired": acquired}).encode() + b"\n")
                            if case in ("busy", "wrong-type"):
                                assert stream.read(1) == b""
                                return
                            challenge = stream.read(33)
                            assert len(challenge) == 33 and challenge[:1] == b"P"
                            events.append("challenge")
                            connection.sendall(
                                b"x" * 32 if case == "bad-challenge" else challenge[1:]
                            )
                            if case == "bad-challenge":
                                assert stream.read(1) == b""
                                return
                            assert stream.read(1) == b"X"
                            events.append("exit")
                            if case != "lost-ack":
                                connection.sendall(b'{"exit_committed":true}\n')
                        finally:
                            stream.close()
                except (OSError, ValueError, AssertionError) as error:
                    failures.append(error)

            thread = threading.Thread(target=serve)
            thread.start()
            result = subprocess.run(
                [str(binary), str(path)],
                capture_output=True,
                text=True,
                check=True,
                timeout=4,
                env={**os.environ, **({"WRONG_UID": "1"} if case == "wrong-uid" else {})},
            )
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert not failures
            assert int(result.stdout) == (
                1 if case == "accepted" else (0 if case == "busy" else -1)
            )
            assert events.count("exit") == (1 if case in ("accepted", "lost-ack") else 0)
            listener.setblocking(False)
            with pytest.raises(BlockingIOError):
                listener.accept()


def test_native_exit_request_real_finite_runtime(binary):
    import select

    from test_client_macos_lease_process import RUNTIME_CHILD

    with tempfile.TemporaryDirectory(prefix="d-native-exit-", dir="/tmp") as directory:
        (Path(directory) / "runtime.lock").touch(mode=0o600)
        child = subprocess.Popen(
            [sys.executable, "-c", RUNTIME_CHILD, directory],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        reaper = threading.Thread(target=child.wait)
        reaper.start()
        try:
            assert select.select([child.stdout], [], [], 5)[0]
            line = child.stdout.readline()
            assert line, child.stderr.read()
            assert json.loads(line)["pid"] == child.pid
            result = subprocess.run(
                [str(binary), str(Path(directory) / "maintenance.sock")],
                env={**os.environ, "WAIT_ORIGINAL": "1"},
                capture_output=True,
                text=True,
                check=True,
                timeout=4,
            )
            assert result.stdout.strip() == "1", result.stderr
            reaper.join(timeout=10)
            assert not reaper.is_alive()
            assert child.returncode == 0, child.stderr.read()
            evidence = json.loads((Path(directory) / "runtime-result.json").read_text())
            assert evidence[-3:] == ["socket_closed", "executor_closed", "journal_closed"]
        finally:
            reaper.join(timeout=10)
            assert not reaper.is_alive()
            child.stdout.close()
            child.stderr.close()


@pytest.mark.parametrize("case", ["same", "reused", "absent", "denied", "short"])
def test_native_original_identity_observation(tmp_path, case):
    native = Path(__file__).parents[1] / "native/client-macos-lease-request.m"
    harness = tmp_path / "observe.m"
    harness.write_text(
        "#include <libproc.h>\n#include <sys/proc_info.h>\n#include <errno.h>\n"
        "static int fake_info(int pid,int flavor,uint64_t arg,void *buffer,int size) {\n"
        " (void)pid;(void)flavor;(void)arg;struct proc_bsdinfo *info=buffer;\n"
        + (
            "errno=ESRCH;return 0;\n"
            if case == "absent"
            else "errno=EPERM;return 0;\n"
            if case == "denied"
            else "return 1;\n"
            if case == "short"
            else f"info->pbi_start_tvsec={11 if case == 'reused' else 10};info->pbi_start_tvusec=2;return size;\n"
        )
        + "}\n#define proc_pidinfo fake_info\n"
        + f'#include "{native}"\n'
        + "int main(void) { (void)lease_request_connected;struct lease_original original={123,10,2};\n"
        + 'printf("%d\\n",lease_wait_original_exit(&original,lease_clock()+0.02));return 0;}\n'
    )
    binary = tmp_path / "observe"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-variable",
            "-Wno-unused-parameter",
            "-fobjc-arc",
            "-framework",
            "Foundation",
            str(harness),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=2)
    assert int(result.stdout) == (1 if case in ("reused", "absent") else -1)


@pytest.mark.parametrize("operation", ["poll", "exit"])
@pytest.mark.parametrize("after", ["expired", "failed", "valid"])
def test_native_observation_rechecks_deadline(tmp_path, operation, after):
    native = Path(__file__).parents[1] / "native/client-macos-lease-request.m"
    harness = tmp_path / "deadline.m"
    harness.write_text(
        "#include <time.h>\n#include <poll.h>\n#include <libproc.h>\n"
        "#include <sys/proc_info.h>\n#include <errno.h>\n"
        "static int observed=0;\n"
        "static int fake_clock(clockid_t kind,struct timespec *value) {\n"
        " (void)kind;value->tv_nsec=0;value->tv_sec=10;\n"
        + (" if(observed)return -1;\n" if after == "failed" else "")
        + (" if(observed)value->tv_sec=12;\n" if after == "expired" else "")
        + " return 0;}\n"
        "static int fake_poll(struct pollfd *fds,nfds_t count,int timeout) {\n"
        " (void)count;(void)timeout;observed=1;fds->revents=POLLIN;return 1;}\n"
        "static int fake_info(int pid,int flavor,uint64_t arg,void *buf,int size) {\n"
        " (void)pid;(void)flavor;(void)arg;(void)buf;(void)size;\n"
        " observed=1;errno=ESRCH;return 0;}\n"
        "#define clock_gettime fake_clock\n#define poll fake_poll\n"
        "#define proc_pidinfo fake_info\n"
        + f'#include "{native}"\n'
        + "int main(void) { (void)lease_request_connected;\n"
        " struct lease_original original={123,10,2};(void)original;\n"
        " (void)lease_wait_original_exit;\n"
        + (
            ' printf("%d\\n",lease_wait(7,POLLIN,11));\n'
            if operation == "poll"
            else ' printf("%d\\n",lease_wait_original_exit(&original,11));\n'
        )
        + " return 0;}\n"
    )
    binary = tmp_path / "deadline"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fobjc-arc",
            "-framework",
            "Foundation",
            str(harness),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=2)
    assert int(result.stdout) == (1 if after == "valid" else 0 if operation == "poll" else -1)
