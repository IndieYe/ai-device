/* Offline prototype: compare immutable trusted source and private staged tree.
 * No ownership/authenticity/ACL/xattr or concurrent-mutator guarantees.
 * Header is private to writer probe, not a production installer API.
 */
#include <dirent.h>

static unsigned long checked_entries;
static unsigned long long checked_bytes;

static int equal_file(const char *left, const char *right, const struct stat *expected) {
    int a = open(left, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    int b = open(right, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    struct stat sa, sb;
    int ok = a >= 0 && b >= 0 && !fstat(a, &sa) && !fstat(b, &sb) &&
        S_ISREG(sa.st_mode) && S_ISREG(sb.st_mode) &&
        sa.st_size == expected->st_size && sb.st_size == sa.st_size;
    char x[65536], y[65536];
    while (ok) {
        ssize_t n = read(a, x, sizeof(x));
        if (n < 0) { ok = 0; break; }
        ssize_t got = 0;
        while (got < n) {
            ssize_t m = read(b, y + got, (size_t)(n - got));
            if (m <= 0) { ok = 0; break; }
            got += m;
        }
        if (!ok || memcmp(x, y, (size_t)n)) { ok = 0; break; }
        if (n == 0) {
            if (read(b, y, 1) != 0) ok = 0;
            break;
        }
    }
    if (a >= 0) close(a);
    if (b >= 0) close(b);
    return ok;
}

static int equal_tree(const char *left, const char *right, unsigned int depth) {
    struct stat a, b;
    if (++checked_entries > 50000 || depth > 64 || lstat(left, &a) || lstat(right, &b)) return 0;
    if ((a.st_mode & S_IFMT) != (b.st_mode & S_IFMT)) return 0;
    if (!S_ISLNK(a.st_mode) && ((a.st_mode & 07777) != (b.st_mode & 07777) ||
                              (a.st_mode & 07022))) return 0;
    if (S_ISREG(a.st_mode)) {
        if (a.st_nlink != 1 || b.st_nlink != 1 || a.st_size < 0 ||
            a.st_size > 250000000) return 0;
        checked_bytes += (unsigned long long)a.st_size;
        if (checked_bytes > 2000000000ULL) return 0;
        return equal_file(left, right, &a);
    }
    if (S_ISLNK(a.st_mode)) {
        char x[PATH_MAX], y[PATH_MAX], resolved[PATH_MAX];
        ssize_t nx = readlink(left, x, sizeof(x)-1), ny = readlink(right, y, sizeof(y)-1);
        if (nx <= 0 || nx >= (ssize_t)sizeof(x)-1 || nx != ny || memcmp(x,y,(size_t)nx)) return 0;
        x[nx] = '\0';
        /* This prototype permits only simple same-directory relative targets.
         * All nine links in the current CPython tree meet this contract.
         * No absolute/.. paths, directory traversal or broken/cyclic targets. */
        if (strchr(x, '/') || !strcmp(x, ".") || !strcmp(x, "..") ||
            !realpath(left, resolved) || !realpath(right, resolved)) return 0;
        return 1;
    }
    if (!S_ISDIR(a.st_mode)) return 0;
    DIR *da = opendir(left), *db = opendir(right);
    if (!da || !db) { if (da) closedir(da); if (db) closedir(db); return 0; }
    int ok = 1;
    unsigned long count_a = 0, count_b = 0;
    struct dirent *entry;
    errno = 0;
    while ((entry = readdir(da))) {
        if (!strcmp(entry->d_name,".") || !strcmp(entry->d_name,"..")) continue;
        char pa[PATH_MAX], pb[PATH_MAX];
        count_a++;
        if (snprintf(pa,sizeof(pa),"%s/%s",left,entry->d_name) >= (int)sizeof(pa) ||
            snprintf(pb,sizeof(pb),"%s/%s",right,entry->d_name) >= (int)sizeof(pb) ||
            !equal_tree(pa,pb,depth+1)) { ok = 0; break; }
        errno = 0;
    }
    if (errno) ok = 0;
    errno = 0;
    while ((entry = readdir(db))) {
        if (!strcmp(entry->d_name,".") || !strcmp(entry->d_name,"..")) continue;
        if (++count_b > 50000) { ok = 0; break; }
    }
    if (errno || count_a != count_b) ok = 0;
    closedir(da); closedir(db);
    return ok;
}
