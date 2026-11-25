import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy.runpod.phase2 import warmup
from deploy.runpod.phase2.runtime_archive import FRONT, activate


class PhaseTwoTests(unittest.TestCase):
    def test_restore_preserves_conflicting_frontend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root / "stage"
            (stage / FRONT).mkdir(parents=True)
            (stage / FRONT / "index.html").write_text("archive")
            dist = root / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("existing")
            with self.assertRaises(FileExistsError):
                activate(stage, root / "runtime", dist)
            self.assertEqual((dist / "index.html").read_text(), "existing")
            self.assertFalse((root / "runtime").exists())

    def test_completed_warmup_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "warmup" / "1-2"
            run.mkdir(parents=True)
            (run / "state.json").write_text(json.dumps({"inference_ready": True}))
            with patch("sys.argv", ["warmup", "run", tmp, "graph.json"]), patch.object(
                warmup, "identity", return_value=("1-2", False)
            ), patch.object(warmup.subprocess, "run") as generate:
                self.assertEqual(warmup.main(), 0)
                generate.assert_not_called()
