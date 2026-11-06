/* Internal only; no production main. Include after guarded writer/recovery.
 * Caller continuously owns all three guards. Source is trusted installer
 * resources; embedded inventory is mandatory. Never accepts a target argument.
 */
#ifndef EMBEDDED_RESOURCE_CHECK
#error registered publication requires embedded inventory
#endif
struct registered_publication_context {
    const char *management;
    uid_t manager;
    NSDictionary *__unsafe_unretained paths;
    struct managed_guards *guards;
};
static int registered_publication_current(void *opaque) {
    struct registered_publication_context *context=opaque;
    return registered_guards_current(context->management,context->manager,
                                     context->paths,context->guards);
}
static int publish_registered_target(const char *management, uid_t manager,
                                     NSDictionary *paths, struct managed_guards *guards,
                                     const char *source) {
    if (!registered_guards_current(management,manager,paths,guards))
        return fail("registered guards changed; publication refused");
    NSString *program = paths[@"program"];
    NSString *parent = [program stringByDeletingLastPathComponent];
    const char *parent_path = [parent fileSystemRepresentation];
    uid_t owner = [paths[@"management_owner"] unsignedIntValue];
    struct stat info;
    if (!parent_path || lstat(parent_path,&info) || !S_ISDIR(info.st_mode) ||
        info.st_uid != owner || (info.st_mode & 0022))
        return fail("program parent unavailable or invalid");
    NSString *stage = [parent stringByAppendingPathComponent:
        [NSString stringWithFormat:@".ai-device-client-stage-%ld",(long)getpid()]];
    struct registered_publication_context context={management,manager,paths,guards};
    return publish_guarded_target_checked(source,[program fileSystemRepresentation],
                                          [stage fileSystemRepresentation],&owner,0,
                                          registered_publication_current,&context);
}
