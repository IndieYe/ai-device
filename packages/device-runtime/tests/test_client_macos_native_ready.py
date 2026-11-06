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
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "ready.m"
    harness.write_text(
        "#include <time.h>\n#include <stdlib.h>\n#include <string.h>\n#include <libproc.h>\n"
        "static int observations=0;\n"
        "static int observed_clock(clockid_t kind,struct timespec *value) {\n"
        ' if(observations>=2 && getenv("OBSERVE_CLOCK")) {\n'
        '  if(!strcmp(getenv("OBSERVE_CLOCK"),"failed"))return -1;\n'
        "  value->tv_sec=9000000000LL;value->tv_nsec=0;return 0;}\n"
        " return clock_gettime(kind,value);}\n"
        "static int observed_info(int pid,int flavor,uint64_t arg,void *buffer,int size) {\n"
        " int result=proc_pidinfo(pid,flavor,arg,buffer,size);observations++;return result;}\n"
        "#define clock_gettime observed_clock\n#define proc_pidinfo observed_info\n"
        f'#include "{native / "client-macos-lease-request.m"}"\n'
        f'#include "{native / "client-macos-ready-request.h"}"\n'
        "int main(int argc,char **argv) {@autoreleasepool {\n"
        " (void)lease_request_connected;(void)lease_wait_original_exit;\n"
        " if(argc!=2)return 64;int fd=socket(AF_UNIX,SOCK_STREAM,0);\n"
        " struct sockaddr_un address={0};address.sun_family=AF_UNIX;\n"
        " if(strlen(argv[1])>=sizeof(address.sun_path))return 64;strcpy(address.sun_path,argv[1]);\n"
        " if(connect(fd,(struct sockaddr *)&address,sizeof(address)))return 70;\n"
        ' int result=ready_request_connected(fd,getuid(),@"B",\n'
        ' @"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",lease_clock()+0.5);\n'
        ' close(fd);printf("%d\\n",result);return 0;}}\n'
    )
    binary = tmp_path / "ready"
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
        "ok",
        "nonce",
        "pid",
        "bool-pid",
        "float-pid",
        "version",
        "build",
        "not-ready",
        "numeric-ready",
        "extra-version",
        "expired",
        "failed",
    ],
)
def test_native_exact_ready_single_status_query(binary, case):
    failures = []
    with tempfile.TemporaryDirectory(prefix="d-nready-", dir="/tmp") as directory:
        path = Path(directory) / "s"
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path))
            listener.listen(2)
            listener.settimeout(2)

            def serve():
                try:
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(2)
                        stream = connection.makefile("rb", buffering=0)
                        try:
                            request = json.loads(stream.readline())
                            assert request["action"] == "status"
                            response = {
                                "upgrade_id": request["upgrade_id"],
                                "pid": os.getpid(),
                                "ready": True,
                                "runtime_version": {
                                    "version": "B",
                                    "build_id": "sha256:" + "a" * 64,
                                },
                            }
                            if case == "nonce":
                                response["upgrade_id"] = "different"
                            elif case == "pid":
                                response["pid"] += 1
                            elif case == "bool-pid":
                                response["pid"] = True
                            elif case == "float-pid":
                                response["pid"] = float(os.getpid())
                            elif case in ("version", "build"):
                                response["runtime_version"][
                                    "version" if case == "version" else "build_id"
                                ] = "other"
                            elif case in ("not-ready", "numeric-ready"):
                                response["ready"] = False if case == "not-ready" else 1
                            elif case == "extra-version":
                                response["runtime_version"]["extra"] = "unexpected"
                            connection.sendall(json.dumps(response).encode() + b"\n")
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
                timeout=3,
                env={
                    **os.environ,
                    **({"OBSERVE_CLOCK": case} if case in ("expired", "failed") else {}),
                },
            )
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert not failures
            assert int(result.stdout) == (1 if case == "ok" else 0)
            listener.setblocking(False)
            with pytest.raises(BlockingIOError):
                listener.accept()


@pytest.mark.parametrize("after", ["expired", "failed", "valid"])
def test_native_immediate_connect_rechecks_deadline(tmp_path, after):
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "connect.m"
    harness.write_text(
        "#include <time.h>\n#include <sys/socket.h>\nstatic int connected=0;\n"
        "static int fake_clock(clockid_t kind,struct timespec *value) {\n"
        " (void)kind;value->tv_sec=10;value->tv_nsec=0;\n"
        + (" if(connected)return -1;\n" if after == "failed" else "")
        + (" if(connected)value->tv_sec=12;\n" if after == "expired" else "")
        + " return 0;}\n"
        "static int fake_connect(int fd,const struct sockaddr *address,socklen_t size) {\n"
        " (void)fd;(void)address;(void)size;connected=1;return 0;}\n"
        "#define clock_gettime fake_clock\n#define connect fake_connect\n"
        + f'#include "{native / "client-macos-lease-request.m"}"\n'
        + f'#include "{native / "client-macos-registered-connect.h"}"\n'
        + "int main(void) {@autoreleasepool {\n"
        " (void)lease_request_connected;(void)lease_wait_original_exit;\n"
        ' int fd=connect_registered_socket(@{@"directory":@"/tmp/not-contacted"},11);\n'
        ' printf("%d\\n",fd>=0);if(fd>=0)close(fd);return 0;}}\n'
    )
    binary = tmp_path / "connect"
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
    assert int(result.stdout) == (1 if after == "valid" else 0)
