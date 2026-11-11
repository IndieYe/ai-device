/* Why a readiness query failed.
 *
 * Lives in its own header, with a guard, because two units need it and they do
 * not include each other: ready-request.h produces the cause, and the upgrade
 * coordinator names it when it gives up. Test harnesses compile one without the
 * other, so a definition reachable only through ready-request.h would leave the
 * coordinator's translation unit unable to name the type at all.
 *
 * The distinction matters because the causes have opposite fixes: a target that
 * never answered needs the job inspected, while one answering with a stale
 * digest is a packaging defect. Reporting one opaque marker for both is what
 * made a signed-package upgrade failure take hours to attribute.
 */
#ifndef AI_DEVICE_READY_CAUSE_H
#define AI_DEVICE_READY_CAUSE_H

typedef enum {
    READY_CAUSE_NONE = 0,
    READY_NO_RESPONSE,
    READY_REPORTED_NOT_READY,
    READY_NONCE_MISMATCH,
    READY_PID_MISMATCH,
    READY_VERSION_MISMATCH,
    READY_PROCESS_CHANGED,
} ready_cause;

/* Name of a cause for logs. Kept beside the enum so a new case cannot be added
 * without a name to print. Marked unused because a unit that compiles this
 * header without logging a give-up would otherwise fail -Werror. */
__attribute__((unused))
static const char *ready_cause_name(ready_cause cause) {
    switch (cause) {
        case READY_CAUSE_NONE: return "none";
        case READY_NO_RESPONSE: return "no valid response";
        case READY_REPORTED_NOT_READY: return "client reported ready=false";
        case READY_NONCE_MISMATCH: return "response nonce mismatch";
        case READY_PID_MISMATCH: return "response pid mismatch";
        case READY_VERSION_MISMATCH: return "runtime_version mismatch";
        case READY_PROCESS_CHANGED: return "process changed during the query";
    }
    return "unknown";
}

#endif
