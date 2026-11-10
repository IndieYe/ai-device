/* What a failed readiness wait reports before giving up.
 *
 * Its own header because the wording is the whole point and it needs no
 * coordinator to reach: the poll loop that calls this runs only after a swap, a
 * reload and up to 120 seconds, so a test that had to go through the loop could
 * not cover this wording at all. Keeping it here means the text is compiled and
 * exercised directly.
 *
 * Requires client-macos-ready-cause.h for the enum and its names.
 */
#ifndef AI_DEVICE_READY_REPORT_H
#define AI_DEVICE_READY_REPORT_H

#include <stdio.h>

#include "client-macos-ready-cause.h"

/* Report a give-up once: which comparison failed, what this build expected, and
 * how long it waited.
 *
 * The cause is the part that was missing. Without it every failure read the
 * same, so a device that never came up and one serving the wrong bytes looked
 * identical -- and telling them apart on a signed package took hours, because
 * the log carried neither digest. The expected identity is included for the
 * same reason: it is one half of the comparison that failed, and it costs
 * nothing here because the caller already has it.
 *
 * Only the expected side is printed. The reported side is not in scope at this
 * call, and inventing a second lookup would put an I/O on the give-up path.
 */
__attribute__((unused))
static void upgrade_report_not_ready(ready_cause cause,int attempts,NSString *version,
                                     NSString *build) {
    fprintf(stderr,"upgrade not ready: %s; expected version %s build %s"
        " (after %d attempts)\n",ready_cause_name(cause),[version UTF8String],
        [build UTF8String],attempts);
}

#endif
