"""Verify editable package and resource behavior from disposable outside directories."""

import json
import subprocess
from pathlib import Path

import pytest

from agent_company.adapters import codex as hook
from tests.support import ROOT, Fixture

pytestmark = pytest.mark.integration


class PackageIntegrationTests(Fixture):
    """Exercise installed local package boundaries without claiming wheel portability."""

    def test_editable_import_and_resources_outside_repository(self) -> None:
        """Load packaged templates and create an issue without repository cwd or PYTHONPATH.

        Raises:
            AssertionError: The subprocess cannot import, load resources or create exact content.
        """
        # Pass a real creation request into an isolated interpreter outside the checkout.
        request = self.req("create")
        script = """
import json
import sys
from importlib.resources import files
from pathlib import Path
from agent_company.lifecycle import task_workspace as core
from agent_company.adapters import codex

request = json.loads(sys.argv[1])
resource = files('agent_company').joinpath('resources/task_workspace/roadmap.md').read_text()
result = core.execute(request)
print(json.dumps({
    'result': result,
    'module_path': str(Path(core.__file__).resolve()),
    'shared_identity': codex.core is core,
    'core_module_names': [name for name, module in sys.modules.copy().items()
                          if getattr(module, '__file__', None) == core.__file__],
    'template': resource.replace('{{issue_id}}', request['issue_id']),
}))
"""
        result = subprocess.run(
            [hook.PYTHON, "-I", "-c", script, json.dumps(request)],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        # Require the editable checkout and a single canonical lifecycle module identity.
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.require_ok(observed["result"])
        self.assertEqual(
            Path(observed["module_path"]), ROOT / "src/agent_company/lifecycle/task_workspace.py"
        )
        self.assertTrue(observed["shared_identity"])
        self.assertEqual(observed["core_module_names"], ["agent_company.lifecycle.task_workspace"])
        # Compare actual creation bytes with the package resource loaded in that process.
        self.assertEqual((self.root / ".task/TEST-1/roadmap.md").read_text(), observed["template"])
