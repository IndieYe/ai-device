"""Create-only LaunchAgent publication, not a service registration command."""

import os
import stat
import tempfile
from pathlib import Path


class PlistPublishError(RuntimeError):
    pass


def publish_agent(account):
    """Publish complete bytes without replacing an existing definition.

    Caller must hold the management lock and finish account/conflict checks.
    The destination directory must already exist; this function never creates
    account trees or loads services. A crash may leave a private .tmp file,
    never a partially written .plist. Published files are never rolled back
    implicitly, because launchd may already have observed them.
    """
    path = account.layout.plist
    directory = path.parent
    temporary = None
    descriptor = None
    published = False
    try:
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != account.layout.uid
            or info.st_mode & 0o022
        ):
            raise PlistPublishError(
                "LaunchAgents directory has incompatible ownership or permissions"
            )
        content = account.layout.render()
        descriptor, name = tempfile.mkstemp(prefix=".ai-device-", suffix=".tmp", dir=directory)
        temporary = Path(name)
        info = os.fstat(descriptor)
        if (info.st_uid, info.st_gid) != (account.layout.uid, account.gid):
            os.fchown(descriptor, account.layout.uid, account.gid)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # link is an atomic create-only publication, unlike replace/rename
        # which could silently overwrite another registration's definition.
        os.link(temporary, path, follow_symlinks=False)
        published = True
    except OSError:
        raise PlistPublishError(
            "LaunchAgent publication failed; existing definition preserved"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                if not published:
                    # Preserve the original failure. Only this invocation's
                    # private temporary name was considered for cleanup.
                    pass
                else:
                    raise PlistPublishError(
                        "LaunchAgent published; temporary cleanup failed"
                    ) from None
    return path
