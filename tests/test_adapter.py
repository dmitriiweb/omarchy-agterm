import shutil
import subprocess
import unittest
from pathlib import Path


class AdapterNodeTests(unittest.TestCase):
    def test_adapter_lifecycle(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not installed; adapter lifecycle tests require Node.js")

        repository = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [node, "--test", str(repository / "tests" / "test_adapter.mjs")],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Node adapter tests failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
