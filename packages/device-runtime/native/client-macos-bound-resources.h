/* Fixed Scripts-relative resources; no argv/HOME/environment path input.
 * Trusted immutable installer resources, not an ACL/hostile-code sandbox.
 * owner must be 0 in the future administrator entry; tests map to getuid().
 */
#ifndef CLIENT_MACOS_BOUND_RESOURCES_H
#define CLIENT_MACOS_BOUND_RESOURCES_H
#include <mach-o/dyld.h>
#include <limits.h>
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <sys/stat.h>
static int bound_directory(const char *path, uid_t owner) {
    struct stat info;
    return !lstat(path,&info) && S_ISDIR(info.st_mode) &&
        info.st_uid == owner && !(info.st_mode & 0022);
}
static inline int locate_bound_resources(char output[PATH_MAX], uid_t owner) {
    char executable[PATH_MAX], resolved[PATH_MAX];
    uint32_t size=sizeof(executable);
    if (_NSGetExecutablePath(executable,&size) || !realpath(executable,resolved)) return 0;
    struct stat info;
    if (lstat(resolved,&info) || !S_ISREG(info.st_mode) || info.st_uid != owner ||
        info.st_nlink != 1 || (info.st_mode & 0022) || !(info.st_mode & 0100)) return 0;
    char *slash=strrchr(resolved,'/');
    if (!slash) return 0;
    *slash=0;
    if (!bound_directory(resolved,owner)) return 0;
    char resources[PATH_MAX];
    if (snprintf(resources,sizeof(resources),"%s/resources",resolved) >= (int)sizeof(resources) ||
        !bound_directory(resources,owner) ||
        snprintf(output,PATH_MAX,"%s/client-tree",resources) >= PATH_MAX ||
        !bound_directory(output,owner)) return 0;
    return 1;
}
#endif
