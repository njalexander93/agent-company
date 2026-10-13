"""Select one local filesystem implementation without importing foreign OS APIs."""

import sys

if sys.platform == "win32":
    from nplet.lifecycle._filesystem_windows import Directory as Directory
else:
    from nplet.lifecycle._filesystem_posix import Directory as Directory
