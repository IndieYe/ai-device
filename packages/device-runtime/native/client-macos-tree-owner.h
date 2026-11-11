/* Caller first verifies embedded inventory. No chown/chmod, no link traversal. */
#ifndef CLIENT_MACOS_TREE_OWNER_H
#define CLIENT_MACOS_TREE_OWNER_H
#include <dirent.h>
#include <sys/stat.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
static int tree_owner_valid(const char *path, uid_t owner, unsigned int depth) {
    struct stat info;
    if (depth > 64 || lstat(path,&info) || info.st_uid != owner) return 0;
    if (S_ISLNK(info.st_mode)) return 1; /* link target is checked by inventory */
    if ((!S_ISDIR(info.st_mode) && !S_ISREG(info.st_mode)) ||
        (info.st_mode & 0022) || (S_ISREG(info.st_mode) && info.st_nlink != 1)) return 0;
    if (!S_ISDIR(info.st_mode)) return 1;
    DIR *directory=opendir(path);
    if (!directory) return 0;
    int valid=1;
    struct dirent *entry;
    errno=0;
    while ((entry=readdir(directory))) {
        if (!strcmp(entry->d_name,".") || !strcmp(entry->d_name,"..")) continue;
        char child[PATH_MAX];
        if (snprintf(child,sizeof(child),"%s/%s",path,entry->d_name) >= (int)sizeof(child) ||
            !tree_owner_valid(child,owner,depth+1)) {valid=0;break;}
        errno=0;
    }
    if (errno) valid=0;
    closedir(directory);
    return valid;
}
#endif
