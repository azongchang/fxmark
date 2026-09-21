// SPDX-License-Identifier: MIT
#include <stdio.h>
#include <stdlib.h>
#include <linux/limits.h>
#include <errno.h>
#include <string.h>
#include <sys/stat.h>

int mkdir_p(const char *path)
{
	char buf[PATH_MAX];
	char *p;
	struct stat st;

	if (!path || !*path)
		return errno = EINVAL;
	if (strlen(path) >= sizeof(buf))
		return errno = ENAMETOOLONG;
	strcpy(buf, path);
	for (p = buf + 1; ; p++) {
		if (*p == '/' || !*p) {
			char saved = *p;
			*p = 0;
			/*
			 * An existing directory is already the result we want; do
			 * not re-create it. Besides avoiding a pointless syscall
			 * this lets callers use a tree that was prepared by
			 * another component (a mount mode that rejects mkdir).
			 */
			if (stat(buf, &st) || !S_ISDIR(st.st_mode)) {
				if (mkdir(buf, 0777) &&
				    (errno != EEXIST || stat(buf, &st) ||
				     !S_ISDIR(st.st_mode)))
					return errno = (errno == EEXIST ? ENOTDIR : errno);
			}
			*p = saved;
			if (!saved)
				break;
		}
	}
	return 0;
}
