/* Single source of truth for upgrade stage codes.
 *
 * The coordinator returns WHERE an attempt stopped, because the on-disk state
 * differs per stage and neither the caller nor the bounded recovery may guess.
 * A bare success flag cannot express "we asked the old instance to exit and do
 * not know whether it did", which is the difference between a safe report and
 * an unsafe retry.
 *
 * Only AFTER_UNLOAD and AFTER_HANDOFF describe a certain state: the job is
 * unloaded, the definition still exists, and (for AFTER_HANDOFF) the locks are
 * provably released because the handoff reported success. Every UNKNOWN_* code
 * means a step with a real side effect was attempted and its outcome was not
 * confirmed; those must never be replayed or auto-recovered.
 */
#ifndef AI_DEVICE_UPGRADE_STAGES_H
#define AI_DEVICE_UPGRADE_STAGES_H

#define UPGRADE_STAGE_OK 0
/* Refused before any irreversible action: all inputs, resources and the
 * management/start/runtime guard set validated, nothing was requested. */
#define UPGRADE_STAGE_REFUSED 1
/* Old-exit lease requested; acceptance/exit not confirmed. Job is still loaded
 * and the program is untouched, so nothing may be loaded again. */
#define UPGRADE_STAGE_EXIT_UNKNOWN 2
/* Unload was attempted and returned failure. Because a failed bootout does not
 * prove the job is still loaded, the job state is unknown. */
#define UPGRADE_STAGE_UNLOAD_UNKNOWN 3
/* Unload confirmed. Job unloaded, definition intact, program still the OLD one.
 * Certain state: the only recoverable stage where publication never started. */
#define UPGRADE_STAGE_AFTER_UNLOAD 4
/* Publication or its verification failed after being attempted. The program
 * tree may already be the NEW one, so loading would silently pick either
 * version; this is unknown, not recoverable. */
#define UPGRADE_STAGE_PUBLISH_UNKNOWN 5
/* Publication confirmed and start/runtime fully released. Load not yet
 * requested. Certain state: the definition can be bootstrapped exactly once.
 *
 * This code must only be reported when `handoff_registered_start` returned
 * success. A handoff that FAILED leaves start/runtime in an unknown state —
 * possibly released, possibly still held by this process, possibly partially
 * closed — and reporting it here made recovery bootstrap a job while the
 * launcher's take of start.lock could still fail. That case is
 * UPGRADE_STAGE_HANDOFF_UNKNOWN below. */
#define UPGRADE_STAGE_AFTER_HANDOFF 6
/* Load attempted, outcome unknown. Replaying it is forbidden. */
#define UPGRADE_STAGE_AFTER_LOAD 7
/* Job loaded and the target is not serving the expected identity. */
#define UPGRADE_STAGE_NOT_READY 8
/* Publication succeeded, then the start/runtime handoff failed partway.
 *
 * The program tree IS the new one (publication was confirmed before this step
 * began), but at least one of start.lock/runtime.lock may still be held here, or
 * may have been released without the release being confirmed. Nothing may be
 * loaded and nothing may be recovered: a bootstrap now would race a launcher
 * that may itself be blocked on a lock this process still owns.
 *
 * Deliberately NOT the same code as AFTER_HANDOFF: the previous revision did
 * conflate them, which turned "we could not release the locks" into "the locks
 * are released, safe to bootstrap". An operator can retry only after deciding
 * that by hand. */
#define UPGRADE_STAGE_HANDOFF_UNKNOWN 9

#endif
