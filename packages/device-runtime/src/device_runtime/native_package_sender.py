"""Candidate Debian package-tree sender; not wired to dispatch or recovery.

A receipt is evidence of one guard's adopted descendants being reaped, not
permission to recover, retry or activate. The caller must durably bind this
protocol and snapshot in an intent and pass the held package lease descriptor.
"""

from . import native_helper as helper

PROTOCOL = "debian-package-tree-subreaper-v1"


def valid_tree_receipt(receipt, expected_intent):
    """Validate structure and intent binding only; never authorize recovery.

    The expected intent must come from the independently checked transaction,
    not from the receipt. A match does not establish origin or durable fsync.
    """
    if (
        not isinstance(expected_intent, dict)
        or expected_intent.get("sender_protocol") != PROTOCOL
        or not isinstance(receipt, dict)
        or type(receipt.get("returncode")) is not int
    ):
        return False
    expected = {
        "schema_version": 1,
        "state": "package_process_tree_reaped",
        "mechanism": "linux-subreaper-v1",
        "intent": expected_intent,
        "returncode": receipt["returncode"],
    }
    return helper._same_json(receipt, expected)

# Runs from the separately verified transaction runtime, not the package runtime
# being modified. Arguments: receipt, JSON intent, candidate package path.
# The caller must pass the package lease fd to this guard. subprocess.run does
# not pass that fd to dpkg; the guard holds it until after receipt publication.
TREE_GUARD = r"""import ctypes,json,os,subprocess,sys,tempfile
from pathlib import Path
if sys.platform!='linux': raise RuntimeError('package tree guard requires Linux')
receipt=Path(sys.argv[1]); intent=json.loads(sys.argv[2])
if not isinstance(intent,dict) or intent.get('sender_protocol')!='debian-package-tree-subreaper-v1':
 raise RuntimeError('package tree intent protocol required')
if os.path.lexists(receipt): raise RuntimeError('package tree receipt already exists')
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(36,1,0,0,0)!=0: raise RuntimeError('subreaper unavailable')
with receipt.with_suffix('').with_suffix('.package.log').open('xb') as log:
 result=subprocess.run(['/usr/bin/dpkg','--install',sys.argv[3]],check=False,stdout=log,stderr=subprocess.STDOUT)
 while True:
  try: os.waitpid(-1,0)
  except InterruptedError: continue
  except ChildProcessError: break
 log.flush(); os.fsync(log.fileno())
value={'schema_version':1,'state':'package_process_tree_reaped','mechanism':'linux-subreaper-v1','intent':intent,'returncode':result.returncode}
fd,tmp=tempfile.mkstemp(prefix='.package-tree-',dir=receipt.parent)
with os.fdopen(fd,'w') as stream:
 json.dump(value,stream,sort_keys=True); stream.flush(); os.fsync(stream.fileno())
os.link(tmp,receipt); os.unlink(tmp)
fd=os.open(receipt.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(fd); os.close(fd)
sys.exit(result.returncode)
"""
