"""Unwired Linux start-sender tree candidate; never authorizes a recovery.

This guard covers only its adopted descendants, not work delegated to systemd.
The caller must independently bind the immutable intent, runtime and leases;
none of those prerequisites or a durable publication is established here.
"""

from . import native_helper as helper

PROTOCOL = "systemd-start-tree-subreaper-v1"


def valid_tree_receipt(receipt, expected_intent):
    """Check exact shape and intent binding, not origin or persistence."""
    if (
        not isinstance(expected_intent, dict)
        or expected_intent.get("sender_protocol") != PROTOCOL
        or not isinstance(receipt, dict)
        or type(receipt.get("returncode")) is not int
    ):
        return False
    return helper._same_json(
        receipt,
        {
            "schema_version": 1,
            "state": "start_process_tree_reaped",
            "mechanism": "linux-subreaper-v1",
            "intent": expected_intent,
            "returncode": receipt["returncode"],
        },
    )


# The intended caller retains execution and package leases in this guard only;
# subprocess.run closes them in systemctl. No current production path invokes it.
# Arguments: receipt path, JSON intent, exact unit name. The caller is responsible
# for validating all arguments and holding the appropriate leases before launch.
TREE_GUARD = r"""import ctypes,json,os,subprocess,sys,tempfile
from pathlib import Path
if sys.platform!='linux': raise RuntimeError('start tree guard requires Linux')
receipt=Path(sys.argv[1]); intent=json.loads(sys.argv[2]); unit=sys.argv[3]
if not isinstance(intent,dict) or intent.get('sender_protocol')!='systemd-start-tree-subreaper-v1':
 raise RuntimeError('start tree intent protocol required')
if os.path.lexists(receipt): raise RuntimeError('start tree receipt already exists')
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(36,1,0,0,0)!=0: raise RuntimeError('subreaper unavailable')
try:
 result=subprocess.run(['/usr/bin/systemctl','start',unit],timeout=120,check=False)
 code=result.returncode
except subprocess.TimeoutExpired:
 code=124
while True:
 try: os.waitpid(-1,0)
 except InterruptedError: continue
 except ChildProcessError: break
value={'schema_version':1,'state':'start_process_tree_reaped','mechanism':'linux-subreaper-v1','intent':intent,'returncode':code}
fd,tmp=tempfile.mkstemp(prefix='.start-tree-',dir=receipt.parent)
with os.fdopen(fd,'w') as stream:
 json.dump(value,stream,sort_keys=True); stream.flush(); os.fsync(stream.fileno())
os.link(tmp,receipt); os.unlink(tmp)
fd=os.open(receipt.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(fd); os.close(fd)
sys.exit(code)
"""
