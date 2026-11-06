/* Internal first-program publication only, NOT a complete installer.
 * Production caller uses root owner and /opt/ai-device-client, a real host
 * conflict adapter, and fixed bound resources. No implicit safe defaults.
 */
struct first_publication_context {
    const char *management;
    uid_t owner;
    int management_fd;
    const char *target;
    int (*conflicts_clear)(void *);
    void *conflicts_context;
};
static int first_publication_current(void *opaque) {
    struct first_publication_context *context=opaque;
    if (!context->conflicts_clear ||
        registration_state(context->management,context->owner,context->management_fd)!=0)
        return 0;
    struct stat info;
    if (!lstat(context->target,&info) || errno!=ENOENT) return 0;
    return context->conflicts_clear(context->conflicts_context) &&
        registration_state(context->management,context->owner,context->management_fd)==0;
}
static int publish_first_program(const char *management,uid_t owner,int management_fd,
                                 const char *target,int (*conflicts_clear)(void *),void *opaque) {
    struct first_publication_context context={management,owner,management_fd,target,conflicts_clear,opaque};
    if (!target || !first_publication_current(&context)) return fail("first installation preflight refused");
    NSString *program=[NSString stringWithUTF8String:target];
    NSString *parent=[program stringByDeletingLastPathComponent];
    if (!bound_directory([parent fileSystemRepresentation],owner))
        return fail("first installation parent invalid");
    char source[PATH_MAX];
    if (!locate_bound_resources(source,owner)) return fail("fixed resources unavailable");
    NSString *stage=[parent stringByAppendingPathComponent:
        [NSString stringWithFormat:@".ai-device-client-stage-%ld",(long)getpid()]];
    return publish_guarded_target_checked(source,target,[stage fileSystemRepresentation],&owner,1,
                                          first_publication_current,&context);
}

/* Explicit no-write continuation after publication but before registration.
 * Does not repair/replace a different or damaged tree. Never called by the
 * create-only publisher; caller must explicitly choose observation.
 */
static inline int observe_unregistered_program(const char *management,uid_t owner,int management_fd,
                                               const char *target,int (*conflicts_clear)(void *),void *opaque) {
    if (!target || !conflicts_clear || registration_state(management,owner,management_fd)!=0)
        return fail("unregistered observation refused");
    struct stat before,after;
    if (lstat(target,&before) || !S_ISDIR(before.st_mode))
        return fail("unregistered target is unknown");
    NSString *program=[NSString stringWithUTF8String:target];
    NSString *parent=[program stringByDeletingLastPathComponent];
    char source[PATH_MAX];
    if (!bound_directory([parent fileSystemRepresentation],owner) ||
        !locate_bound_resources(source,owner) || !verify_resource_tree(source) ||
        !tree_owner_valid(source,owner,0) || !conflicts_clear(opaque) ||
        registration_state(management,owner,management_fd)!=0 ||
        !verify_resource_tree(target) || !tree_owner_valid(target,owner,0) ||
        !conflicts_clear(opaque) || registration_state(management,owner,management_fd)!=0 ||
        !verify_resource_tree(target) || !tree_owner_valid(target,owner,0) ||
        lstat(target,&after) || before.st_dev!=after.st_dev || before.st_ino!=after.st_ino)
        return fail("unregistered target is not an exact continuation");
    return 0;
}
