#!/bin/sh
# Disposable Linux fixture; no real package update or Device service.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = update-prepare-fence-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 5 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/packages/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
cp "$3" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
cp "$4" "$B/lib/python3.13/site-packages/device_runtime/native_update.py"
cp "$5" "$B/lib/python3.13/site-packages/device_runtime/native_update_job.py"
"$B/bin/python3.13" -I -B - <<'PY'
import os, tempfile
from contextlib import contextmanager
from pathlib import Path
from device_runtime import native_helper as h, native_update as u, native_update_job as job
with tempfile.TemporaryDirectory(prefix='device-prep-fence-',dir='/root') as d:
    root=Path(d); registry=root/'registry'; registry.mkdir(mode=0o700)
    instance=root/'instance'; instance.mkdir(mode=0o700); (instance/'active.json').write_text('{}')
    (registry/'probe.json').write_text('{}'); package=root/'candidate.deb'; package.write_bytes(b'test')
    class Helper:
        @contextmanager
        def _lease(self, instance_id): yield
        def _binding(self, instance_id): return {'root':str(instance),'instance_id':instance_id}
    class Packages:
        def __init__(self): self.calls=0
        def metadata(self,path):
            self.calls+=1
            try:
                with h.package_lease(registry,exclusive=True): raise AssertionError('publisher entered')
            except h.HelperError as exc: assert 'still executing' in str(exc)
            return {'Package':'ai-device-native-client','Version':'2','Architecture':'amd64'}
        def installed(self,name): return {'status':'fixture'}
    nojob=root/'nojob'; nojob.mkdir(mode=0o700)
    class NoBinding(Helper):
        def _binding(self, instance_id): raise AssertionError('binding read before denial')
    denied_manager=u.Updates(nojob,packages=u.Debian(),helper=NoBinding())
    try: job.dispatch_job(denied_manager,'probe','new',runner=lambda *a,**k: (_ for _ in ()).throw(AssertionError('job ran')))
    except job.JobError as exc: assert 'new dispatch disabled' in str(exc)
    else: raise AssertionError('new dispatch accepted')
    assert not (nojob/'updates').exists()
    print('NEW_JOB_DENIED_BEFORE_UPDATE_DIRECTORY_OR_BINDING')
    class ObservationPackages:
        def metadata(self, path): raise AssertionError('unexpected metadata')
        def installed(self, name): raise AssertionError('unexpected installed query')
    missing=root/'missing'; missing.mkdir(mode=0o700)
    observer=u.Updates(missing,packages=ObservationPackages(),helper=Helper())
    before=set(missing.iterdir())
    try: observer.reconcile('probe','one')
    except u.UpdateError as exc: assert 'update transaction directory missing' in str(exc)
    else: raise AssertionError('missing transaction accepted')
    assert set(missing.iterdir())==before and not (missing/'updates').exists()
    unsafe=root/'unsafe'; unsafe.mkdir(mode=0o700); (unsafe/'updates').symlink_to(missing)
    observer=u.Updates(unsafe,packages=ObservationPackages(),helper=Helper())
    try: observer.reconcile('probe','one')
    except h.HelperError as exc: assert 'administrator-owned' in str(exc)
    else: raise AssertionError('symlink transaction directory accepted')
    assert (unsafe/'updates').is_symlink() and set(missing.iterdir())==before
    print('RECONCILE_MISSING_OR_SYMLINK_DIRECTORY_REFUSED_WITHOUT_INITIALIZATION')
    packages=Packages(); manager=u.Updates(registry,packages=packages,helper=Helper())
    marker=registry/'query-supervisor.intent.json'; marker.symlink_to('missing')
    try: manager.prepare('probe','one',package,sha256=h._digest(package),version='2')
    except u.UpdateError as exc: assert 'query supervisor intent remains unknown' in str(exc)
    else: raise AssertionError('unknown intent accepted')
    assert not (registry/'updates').exists() and packages.calls==0 and marker.is_symlink()
    print('DANGLING_INTENT_PREPARE_REFUSED_BEFORE_DIRECTORY_OR_METADATA')
    # Separate registry: do not delete or alter unknown intent to continue.
    clean=root/'clean'; clean.mkdir(mode=0o700); (clean/'probe.json').write_text('{}')
    registry=clean; manager=u.Updates(registry,packages=packages,helper=Helper())
    manager.prepare('probe','one',package,sha256=h._digest(package),version='2')
    prepared=registry/'updates/5-probe--one.prepared.json'; before=prepared.read_bytes()
    marker=registry/'query-supervisor.intent.json'; marker.write_bytes(b'unknown')
    metadata_before=packages.calls
    try: manager.apply('probe','one')
    except u.UpdateError as exc: assert 'query supervisor intent remains unknown' in str(exc)
    else: raise AssertionError('new apply accepted unknown intent')
    assert packages.calls==metadata_before and not (registry/'updates/5-probe--one.apply-intent.json').exists()
    print('NEW_APPLY_REFUSED_BEFORE_METADATA_OR_INTENT')
    try: manager.approve('probe','one',prepared_sha256=h._digest(prepared))
    except u.UpdateError as exc: assert 'query supervisor intent remains unknown' in str(exc)
    else: raise AssertionError('unknown intent accepted')
    assert prepared.read_bytes()==before and not (registry/'updates/5-probe--one.approved.json').exists()
    assert marker.read_bytes()==b'unknown' and packages.calls==1
    print('PREPARE_METADATA_HOLDS_SH_AND_APPROVAL_REFUSES_WITHOUT_WRITES')
    # Observe an independently prepared transaction; never remove the unknown marker.
    observed=root/'observed'; observed.mkdir(mode=0o700); (observed/'probe.json').write_text('{}')
    registry=observed; manager=u.Updates(registry,packages=packages,helper=Helper())
    manager.prepare('probe','one',package,sha256=h._digest(package),version='2')
    stem=registry/'updates/5-probe--one'
    manager.approve('probe','one',prepared_sha256=h._digest(stem.with_suffix('.prepared.json')))
    stem.with_suffix('.apply-intent.json').write_text('{}')
    marker=registry/'query-supervisor.intent.json'; marker.write_bytes(b'unknown')
    class ObservedPackages:
        def __init__(self): self.calls=[]
        def metadata(self,path): self.calls.append('metadata'); return {'Package':'ai-device-native-client','Version':'2','Architecture':'amd64'}
        def installed(self,name): self.calls.append('installed'); return {'status':'fixture'}
        def apply(self,*args,**kwargs): raise AssertionError('replayed package action')
    observer=ObservedPackages(); manager.packages=observer
    before={p.name:p.read_bytes() for p in stem.parent.iterdir()}
    old_fsync=os.fsync
    try:
        os.fsync=lambda *_: (_ for _ in ()).throw(AssertionError('observation fsync'))
        result=manager.apply('probe','one')
    finally: os.fsync=old_fsync
    assert result['apply_intent']=={} and observer.calls==['metadata','installed']
    assert {p.name:p.read_bytes() for p in stem.parent.iterdir()}==before and marker.read_bytes()==b'unknown'
    print('EXISTING_APPLY_OBSERVES_WITHOUT_DIRECTORY_INIT_OR_REPLAY')
    class JobManager:
        packages=u.Debian()
        registry=registry
        def _prepared(self,*args): raise AssertionError('metadata preparation executed')
    jobstem=registry/'updates/5-probe--new'
    try: job._prepare_dispatch_locked(JobManager(),'probe','new',{},jobstem)
    except job.JobError as exc: assert 'new dispatch disabled' in str(exc)
    else: raise AssertionError('new job dispatch not denied')
    assert not os.path.lexists(jobstem.with_suffix('.job-intent.json'))
    print('NEW_JOB_DENIED_BEFORE_METADATA_OR_JOB_INTENT')
    print('FAKE_PACKAGES_NO_DEVICE_SERVICE_OR_EXTERNAL_PROCESS_TREE_PROOF')
PY
