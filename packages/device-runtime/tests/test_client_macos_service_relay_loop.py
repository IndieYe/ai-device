"""Relay wait-loop bounds, exercised with a REAL parent/child process pair.

The previous version substituted the helper and returned before the spawn or
read loop ever ran (the bound tree did not exist, so posix_spawn failed
immediately), so it proved nothing about the deadline or reaping. This version
builds a private tree in tmp_path, opens a REAL management lock, and spawns a
real child through the production code path using a fake `python3` helper.

Never calls launchctl and never touches a real service.

The clock is injected (the header keeps a `service_relay_clock` seam) so
deadlines are deterministic. Wall-clock duration is asserted separately: a
blocking read would still end in a refusal but only after the helper's 30s
sleep, which the duration bound rejects.
"""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin compiler")

NATIVE = Path(__file__).parents[1] / "native"

HEADER = textwrap.dedent(
    """\
    #import <Foundation/Foundation.h>
    #include <fcntl.h>
    #include <math.h>
    #include <stdio.h>
    #include <string.h>
    #include <sys/file.h>
    #include <sys/stat.h>
    #include <unistd.h>
    struct managed_guards {int management,start,runtime;NSDictionary *__strong identity;};

    /* Injected clock: advances a fixed step per read so a deadline is reached
     * after a known number of loop iterations, and reset per case. */
    static double fake_now=100.0;
    static int clock_calls=0;
    static double test_clock(void){clock_calls++;double v=fake_now;fake_now+=0.002;return v;}
    static void reset_clock(void){fake_now=100.0;}
    __attribute__((unused)) static void reset_clock_ref(void){reset_clock();}
    #define service_relay_clock test_clock
    """
)

# The helper is selected by the module argument (argv[4]: python3 -I -B -m MODULE
# --inherit-management-lock) so a single fake python3 covers every case without
# the relay seeing a different program. It records its own PID before doing any
# other work, so a caller can verify afterwards that the relay reaped it.
HELPER = """#!/bin/sh
echo $$ > "{pidfile}"
case "$4" in
  mod_token) printf 'TOKEN_OK'; exit 0 ;;
  mod_silent) exec sleep 30 ;;
  mod_noise) printf 'TOKEN_OK'; printf 'extra'; exit 0 ;;
  mod_fail) printf 'TOKEN_OK'; exit 3 ;;
  mod_eofonly) exit 0 ;;
  mod_fdcheck)
    # fd 4 must be the management lock handed over by the relay, not /dev/null
    # or a pipe end. Compare its inode against the real lock file.
    if [ "$(stat -f %i /dev/fd/4 2>/dev/null)" = "$(stat -f %i '{lockfile}' 2>/dev/null)" ] \
       && [ -n "$(stat -f %i /dev/fd/4 2>/dev/null)" ]; then
      printf 'TOKEN_OK'; exit 0
    fi
    printf 'BAD_FD'; exit 0 ;;
esac
exec sleep 30
"""


def build(tmp_path, name, run_cases):
    """Compile a harness whose main() drives the supplied run_cases()."""
    root = tmp_path / "tree"
    (root / "runtime" / "bin").mkdir(parents=True, exist_ok=True)
    pidfile = tmp_path / "child.pid"
    helper = root / "runtime" / "bin" / "python3"
    helper.write_text(HELPER.format(pidfile=pidfile,
                                    lockfile=tmp_path / "management" / "management.lock"))
    helper.chmod(0o755)

    (tmp_path / "management").mkdir(exist_ok=True)

    harness = tmp_path / f"{name}.m"
    harness.write_text(
        HEADER
        + f'static const char *TEST_ROOT = "{root}";\n'
        + textwrap.dedent(
            """\
            static int locate_bound_resources(char *p,uid_t o){(void)o;
                strcpy(p,TEST_ROOT);return 1;}
            static int verify_resource_tree(const char *p){(void)p;return 1;}
            static int tree_owner_valid(const char *p,uid_t o,int d){
                (void)p;(void)o;(void)d;return 1;}
            """
        )
        + f'#include "{NATIVE}/client-macos-private-lock.h"\n'
        + f'#include "{NATIVE}/client-macos-service-relay.h"\n'
        + run_cases
        + "\n"
        + "int main(void){@autoreleasepool{(void)load_registered_service_relay;"
          "(void)unload_registered_service_relay;"
          "(void)request_bound_registered_start;"
          "(void)acquire_private_lock;return run_cases();}}\n"
    )
    binary = tmp_path / name
    compiled = subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-fobjc-arc",
         "-framework", "Foundation", "-I", str(NATIVE), str(harness), "-o", str(binary)],
        check=False, capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    return binary


def run(binary):
    started = time.monotonic()
    result = subprocess.run([str(binary)], check=False, capture_output=True,
                            text=True, timeout=90)
    elapsed = time.monotonic() - started
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "LOCKFAIL" not in result.stdout, result.stdout
    rows = {row.split("\t")[0]: row.split("\t")
            for row in result.stdout.splitlines() if row}
    return rows, elapsed


def _child_alive(pidfile):
    """True when the helper recorded by the pidfile is still running."""
    if not pidfile.exists():
        return None
    pid = int(pidfile.read_text().strip())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_bounds_are_enforced_with_a_real_child(tmp_path):
    """Deadline cuts off a silent child, reaps it, and keeps the lock."""
    mgmt = tmp_path / "management"
    body = textwrap.dedent(
        f"""\
        static int run_cases(void){{
            uid_t me=getuid();
            mkdir("{mgmt}",0700);chmod("{mgmt}",0700);
            int created=open("{mgmt}/management.lock",O_CREAT|O_RDWR,0600);
            if(created<0){{perror("create");return 2;}}
            close(created);
            int mgmt=acquire_private_lock("{mgmt}","management.lock",me);
            if(mgmt<0){{printf("LOCKFAIL\\n");return 2;}}
            int rc;

            /* Silent helper: only the deadline can end this, and the child
             * sleeps 30s, so a blocking read would take 30s to give up. */
            reset_clock();
            rc=service_relay_relay("{mgmt}",me,mgmt,"mod_silent","TOKEN_OK",100.25);
            printf("silent\\t%d\\t%d\\n",rc,
                   private_lock_held(mgmt,"{mgmt}","management.lock",me));

            reset_clock();
            rc=service_relay_relay("{mgmt}",me,mgmt,"mod_token","TOKEN_OK",110.0);
            printf("token\\t%d\\n",rc);

            reset_clock();
            rc=service_relay_relay("{mgmt}",me,mgmt,"mod_noise","TOKEN_OK",110.0);
            printf("noise\\t%d\\n",rc);

            reset_clock();
            rc=service_relay_relay("{mgmt}",me,mgmt,"mod_fail","TOKEN_OK",110.0);
            printf("fail\\t%d\\n",rc);

            reset_clock();
            rc=service_relay_relay("{mgmt}",me,mgmt,"mod_eofonly","TOKEN_OK",110.0);
            printf("eofonly\\t%d\\n",rc);
            return 0;
        }}
        """
    )
    rows, elapsed = run(build(tmp_path, "relay-real", body))

    # Deadline path: refused, and the management lock survives the timeout.
    assert rows["silent"][1] == "0", rows
    assert rows["silent"][2] == "1", rows
    # Exact token with a clean exit is the only success shape.
    assert rows["token"][1] == "1", rows
    # Trailing bytes, nonzero exit and a bare EOF all fail closed.
    assert rows["noise"][1] == "0", rows
    assert rows["fail"][1] == "0", rows
    assert rows["eofonly"][1] == "0", rows

    # The silent helper must be abandoned promptly, not waited out. Without the
    # non-blocking read end this case alone would take the helper's 30s sleep.
    assert elapsed < 15.0, f"relay did not honor its deadline ({elapsed:.1f}s)"


def test_timeout_reaps_the_child(tmp_path):
    """After the deadline the helper must be gone, not left running."""
    mgmt = tmp_path / "management"
    pidfile = tmp_path / "child.pid"
    body = textwrap.dedent(
        f"""\
        static int run_cases(void){{
            uid_t me=getuid();
            mkdir("{mgmt}",0700);chmod("{mgmt}",0700);
            int created=open("{mgmt}/management.lock",O_CREAT|O_RDWR,0600);
            if(created<0){{perror("create");return 2;}}
            close(created);
            int mgmt=acquire_private_lock("{mgmt}","management.lock",me);
            if(mgmt<0){{printf("LOCKFAIL\\n");return 2;}}
            printf("held_before\\t%d\\n",
                   private_lock_held(mgmt,"{mgmt}","management.lock",me));
            reset_clock();
            /* Deadline big enough that the helper definitely starts and writes
             * its pidfile; the point of this case is reaping, not tightness. */
            int rc=service_relay_relay("{mgmt}",me,mgmt,"mod_silent","TOKEN_OK",100.6);
            printf("silent\\t%d\\t%d\\n",rc,
                   private_lock_held(mgmt,"{mgmt}","management.lock",me));
            return 0;
        }}
        """
    )
    rows, elapsed = run(build(tmp_path, "relay-reap", body))
    assert rows["held_before"][1] == "1", rows
    assert rows["silent"][1] == "0", rows
    assert elapsed < 15.0, elapsed

    # The relay must have terminated and reaped the silent helper. A child that
    # is merely abandoned would still answer signal 0 here.
    assert pidfile.exists(), "helper never started"
    pid = int(pidfile.read_text().strip())
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(pid, signal.SIGKILL)  # do not leak a stray 30s sleeper
        raise AssertionError(f"relay left child {pid} running after its deadline")


def test_management_fd_survives_low_descriptor_layout(tmp_path):
    """The inherited lock must not land on a descriptor the spawn actions reuse.

    Regression for a real defect: the relay duplicated the management lock with
    F_DUPFD_CLOEXEC starting at 0. With stdin/stdout/stderr closed and the lock
    on fd 4, the pipe takes 0/1 and the duplicate lands on 2 -- exactly the fd
    that posix_spawn_file_actions_addopen(STDERR) then overwrites, so the child
    received a /dev/null handle instead of the management lock. start-child.h
    already used base 10 for this reason. The helper here asserts that fd 4 in
    the child is genuinely the lock file.
    """
    mgmt = tmp_path / "management"
    body = textwrap.dedent(
        f"""\
        static int run_cases(void){{
            uid_t me=getuid();
            mkdir("{mgmt}",0700);chmod("{mgmt}",0700);
            int created=open("{mgmt}/management.lock",O_CREAT|O_RDWR,0600);
            if(created<0){{perror("create");return 2;}}
            close(created);
            /* Put the lock on a low fd, then close every standard descriptor so
             * the duplicate/pipe allocation is forced into 0/1/2. */
            int mgmt=open("{mgmt}/management.lock",O_RDWR|O_NOFOLLOW);
            if(mgmt<0){{printf("OPENFAIL\\n");return 2;}}
            while(mgmt<4){{int next=dup(mgmt);if(next<0){{printf("DUPFAIL\\n");return 2;}}mgmt=next;}}
            if(flock(mgmt,LOCK_EX|LOCK_NB)){{printf("LOCKFAIL\\n");return 2;}}
            int keep=fopen("/dev/null","w")?1:1;
            FILE *out=fdopen(dup(1),"w");
            if(!out) out=stderr;
            fclose(stdin);fclose(stdout);fclose(stderr);
            (void)keep;
            reset_clock();
            int rc=service_relay_relay("{mgmt}",me,mgmt,"mod_fdcheck","TOKEN_OK",110.0);
            fprintf(out,"fdcheck\\t%d\\n",rc);
            fflush(out);
            return 0;
        }}
        """
    )
    rows, _ = run(build(tmp_path, "relay-fd", body))
    assert rows["fdcheck"][1] == "1", rows


def test_nan_clock_refuses_before_spawn(tmp_path):
    """A NaN clock reading must refuse at the entry, not after spawning.

    Regression: the entry compared an unvalidated clock twice. NaN makes both
    `<0` and `>=deadline` false, so the relay continued through resource
    verification and posix_spawn; only the wait loop noticed. The clock read
    count is the observable: a clean entry check reads it exactly once, while
    the defective version reaches the loop and spawns a helper.
    """
    mgmt = tmp_path / "management"
    body = textwrap.dedent(
        f"""\
        static int run_cases(void){{
            uid_t me=getuid();
            mkdir("{mgmt}",0700);chmod("{mgmt}",0700);
            int created=open("{mgmt}/management.lock",O_CREAT|O_RDWR,0600);
            if(created<0){{perror("create");return 2;}}
            close(created);
            int mgmt=acquire_private_lock("{mgmt}","management.lock",me);
            if(mgmt<0){{printf("LOCKFAIL\\n");return 2;}}
            fake_now=NAN;
            int before=clock_calls;
            int rc=service_relay_relay("{mgmt}",me,mgmt,"mod_token","TOKEN_OK",110.0);
            printf("nan\\t%d\\t%d\\n",rc,clock_calls-before);
            return 0;
        }}
        """
    )
    rows, _ = run(build(tmp_path, "relay-nan", body))
    assert rows["nan"][1] == "0", rows
    # Exactly one clock read: refused before resource/spawn work began.
    assert rows["nan"][2] == "1", rows


def test_released_lock_refuses_before_any_spawn(tmp_path):
    mgmt = tmp_path / "management"
    body = textwrap.dedent(
        f"""\
        static int run_cases(void){{
            uid_t me=getuid();
            mkdir("{mgmt}",0700);chmod("{mgmt}",0700);
            int created=open("{mgmt}/management.lock",O_CREAT|O_RDWR,0600);
            if(created<0){{perror("create");return 2;}}
            close(created);
            int mgmt=acquire_private_lock("{mgmt}","management.lock",me);
            if(mgmt<0){{printf("LOCKFAIL\\n");return 2;}}
            /* Still the same file, but no longer held: matches() stays 1. */
            flock(mgmt,LOCK_UN);
            printf("matches\\t%d\\n",
                   private_lock_matches(mgmt,"{mgmt}","management.lock",me));
            printf("held\\t%d\\n",
                   private_lock_held(mgmt,"{mgmt}","management.lock",me));
            reset_clock();
            int rc=service_relay_relay("{mgmt}",me,mgmt,"mod_token","TOKEN_OK",110.0);
            printf("relay\\t%d\\n",rc);
            close(mgmt);
            return 0;
        }}
        """
    )
    rows, _ = run(build(tmp_path, "relay-released", body))

    assert rows["matches"][1] == "1", rows   # old check: would have passed
    assert rows["held"][1] == "0", rows
    assert rows["relay"][1] == "0", rows     # new check: refuses
