"""Select one local filesystem implementation without importing foreign OS APIs."""

import sys

if sys.platform == "win32":
    from agent_company.lifecycle._filesystem_windows import Directory as Directory
else:
    from agent_company.lifecycle._filesystem_posix import Directory as Directory
