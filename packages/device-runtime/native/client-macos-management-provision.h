/* Internal create-only preparation. Production caller must supply the fixed
 * root management path. Never repair, chmod, unlink or replace an existing lock.
 * Failure may leave a new private directory/lock for explicit retry.
 */
#ifndef CLIENT_MACOS_MANAGEMENT_PROVISION_H
#define CLIENT_MACOS_MANAGEMENT_PROVISION_H
static int provision_management_lock(const char *directory,uid_t owner) {
    if (!directory || !*directory || strlen(directory)>=PATH_MAX) return -1;
    char parent_path[PATH_MAX];strcpy(parent_path,directory);
    char *slash=strrchr(parent_path,'/');
    if (!slash || !slash[1]) return -1;
    if (slash==parent_path) slash[1]=0;else *slash=0;
    struct stat parent,after;
    if (lstat(parent_path,&parent) || !S_ISDIR(parent.st_mode) ||
        parent.st_uid!=owner || (parent.st_mode & 0022)) return -1;
    if (mkdir(directory,0700) && errno!=EEXIST) return -1;
    if (lstat(directory,&after) || !S_ISDIR(after.st_mode) ||
        after.st_uid!=owner || (after.st_mode & 0077)) return -1;
    char path[PATH_MAX];
    if (snprintf(path,sizeof(path),"%s/management.lock",directory)>=(int)sizeof(path)) return -1;
    int created=open(path,O_CREAT|O_EXCL|O_RDWR|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC,0600);
    if (created<0 && errno!=EEXIST) return -1;
    if (created>=0) {
        if (fsync(created)) {close(created);return -1;}
        close(created);
    }
    int fd=acquire_private_lock(directory,"management.lock",owner);
    if (fd<0) return fd;
    if (lstat(parent_path,&after) || !S_ISDIR(after.st_mode) ||
        parent.st_dev!=after.st_dev || parent.st_ino!=after.st_ino ||
        after.st_uid!=owner || (after.st_mode & 0022) ||
        !private_lock_matches(fd,directory,"management.lock",owner)) {close(fd);return -1;}
    return fd;
}
#endif
