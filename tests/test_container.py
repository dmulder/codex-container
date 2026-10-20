import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wrapper-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install = self.root / "install with spaces"
        self.install.mkdir()
        shutil.copy2(ROOT / "codex", self.install / "codex")
        (self.install / ".packages").write_text("curl\nnodejs24 npm24 # comment\n")
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        self.home = self.root / "host-home"
        self.home.mkdir()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        shutil.copy2(ROOT / "tests/fake_podman.py", bin_dir / "podman")
        (bin_dir / "podman").chmod(0o755)
        self.state = self.root / "fake-state"
        self.env = {**os.environ, "HOME": str(self.home),
                    "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                    "CODEX_PRO_HOME": str(self.root / "data"),
                    "XDG_CACHE_HOME": str(self.root / "cache"),
                    "FAKE_PODMAN_STATE": str(self.state)}
        for name in ("CODEX_PRO_PROFILE", "FAKE_FAIL", "FAKE_BLOCK", "FAKE_VET"):
            self.env.pop(name, None)

    def run_wrapper(self, args=None, env=None, cwd=None):
        return subprocess.run([str(self.install / "codex"), *(args or ["exec", "-"])],
                              cwd=cwd or self.project, env=env or self.env,
                              input="Review supplied input\n", text=True,
                              capture_output=True, timeout=15)

    def info(self):
        return json.loads((self.state / "last-exec.json").read_text())

    def calls(self):
        return [json.loads(line) for line in (self.state / "calls.jsonl").read_text().splitlines()]

    def shared_home(self):
        return self.root / "data"

    def recovery_record(self):
        import hashlib
        digest = hashlib.sha256(os.fsencode(self.shared_home())).hexdigest()
        return next((self.root / "cache/codex-pro/state" / digest).glob("*.cleanup-pending.json"))

    def assert_no_containers(self):
        self.assertEqual(list(self.state.glob("codex-pro-*.json")), [])
        self.assertEqual(list(self.state.glob("*.pid")), [])

    def start_blocked(self, stage="exec", profile=None):
        env = {**self.env, "FAKE_BLOCK": stage}
        if profile:
            env["CODEX_PRO_PROFILE"] = profile
        process = subprocess.Popen([str(self.install / "codex"), "exec", "-"],
                                   cwd=self.project, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.finish_process, process)
        process.stdin.write("review\n")
        process.stdin.flush()
        deadline = time.monotonic() + 8
        while not (self.state / "blocked").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail(f"Wrapper did not reach blocked {stage}")
            time.sleep(0.02)
        return process

    @staticmethod
    def finish_process(process):
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)

    def test_default_isolation_and_stdout(self):
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "CODEX STDOUT\n")
        self.assertIn("BUILD DIAGNOSTIC", result.stderr)
        self.assertIn("UPDATE DIAGNOSTIC", result.stderr)
        args = self.info()["args"]
        self.assertIn(f"{self.shared_home()}:/home/codex:rw", args)
        self.assertIn(f"{self.project}:/workspace:rw", args)
        self.assertIn(f"{self.project}:{self.project}:rw", args)
        self.assertIn("CODEX_SQLITE_HOME=/home/codex/.codex", args)
        self.assertIn("XDG_RUNTIME_DIR=/tmp/codex-runtime", args)
        self.assertIn("--pid=private", args)
        self.assertIn("--ipc=private", args)
        self.assertNotIn("-t", args)
        self.assertEqual(self.info()["stdin"], "Review supplied input\n")
        self.assertFalse(any(arg.startswith("/tmp:/tmp:") for arg in args))
        self.assert_no_containers()

    def test_shared_home_across_commands_projects_and_symlink_launch(self):
        other = self.root / "other-project"
        other.mkdir()
        for args, cwd, profile in [(["--help"], self.project, "interactive"),
                                   (["exec", "-"], other, "exec"),
                                   (["-m", "example", "exec", "-"], self.project, "exec")]:
            result = self.run_wrapper(args, cwd=cwd)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"{self.shared_home()}:/home/codex:rw", self.info()["args"])
        link = self.root / "launcher"
        link.symlink_to(self.install / "codex")
        result = subprocess.run([str(link), "exec", "-"], cwd=self.project, env=self.env,
                                input="x", capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(call[0] == "build" for call in self.calls()), 1)

    def test_terminal_allocated_only_when_both_streams_are_terminals(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        process = subprocess.Popen([str(self.install / "codex"), "exec", "-"],
                                   cwd=self.project, env=self.env, stdin=slave, stdout=slave,
                                   stderr=subprocess.PIPE, text=True)
        os.close(slave)
        self.addCleanup(self.finish_process, process)
        os.write(master, b"review\n")
        _, errors = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, errors)
        self.assertIn("-t", self.info()["args"])
        self.assertEqual(self.info()["stdin"], "review\n")

    def test_symlink_uses_only_physical_launcher_configuration(self):
        source = self.root / "reference.txt"
        source.write_text("reference")
        (self.install / ".files").write_text(f"ro\t{source}\n")
        # Decoys beside the symlink and in the working directory must be ignored.
        (self.root / ".packages").write_text("invalid;package\n")
        (self.root / ".files").write_text("invalid rule\n")
        (self.project / ".packages").write_text("invalid;package\n")
        (self.project / ".files").write_text("invalid rule\n")
        link = self.root / "launcher"
        link.symlink_to(self.install / "codex")

        result = subprocess.run([str(link), "exec", "-"], cwd=self.project,
                                env=self.env, input="piped prompt\n", text=True,
                                capture_output=True, timeout=15)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.info()["stdin"], "piped prompt\n")
        self.assertIn(f"{source}:{source}:ro", self.info()["args"])
        content = (self.root / "cache/codex-pro/Containerfile").read_text()
        self.assertIn("        curl nodejs24 npm24 &&", content)

    def test_embedded_recipe_preserves_current_image(self):
        result = self.run_wrapper()

        self.assertEqual(result.returncode, 0, result.stderr)
        content = (self.root / "cache/codex-pro/Containerfile").read_bytes()
        # Pinned from the pre-embedding recipe rendered with the fixture packages.
        self.assertEqual(hashlib.sha256(content).hexdigest(),
                         "692aede03fab4e09988f123ef4318f12a68f20bc7454391ea40006e58886accb")

    def test_package_changes_rebuild_image(self):
        self.assertEqual(self.run_wrapper().returncode, 0)
        (self.install / ".packages").write_text("curl nodejs24 npm24 git\n")

        result = self.run_wrapper()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(call[0] == "build" for call in self.calls()), 2)
        content = (self.root / "cache/codex-pro/Containerfile").read_text()
        self.assertIn("        curl nodejs24 npm24 git &&", content)

    def test_package_comments_and_whitespace_reuse_image(self):
        self.assertEqual(self.run_wrapper().returncode, 0)
        (self.install / ".packages").write_text(
            "# Same packages\n\n curl  nodejs24\t npm24 # new comment\n")

        result = self.run_wrapper()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(call[0] == "build" for call in self.calls()), 1)

    def test_terminal_input_with_captured_output_does_not_allocate_terminal(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        process = subprocess.Popen([str(self.install / "codex"), "exec", "-"],
                                   cwd=self.project, env=self.env, stdin=slave, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        os.close(slave)
        self.addCleanup(self.finish_process, process)
        os.write(master, b"review\n")
        _, errors = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, errors)
        self.assertNotIn("-t", self.info()["args"])

    def test_existing_shared_state_is_preserved(self):
        codex_home = self.shared_home() / ".codex"
        codex_home.mkdir(parents=True)
        auth = codex_home / "auth.json"
        auth.write_text('{"test_credential": "existing"}')
        old_profile = self.shared_home() / "projects/old/interactive/.codex"
        old_profile.mkdir(parents=True)
        session = old_profile / "session.txt"
        session.write_text("preserve")
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(auth.read_text(), '{"test_credential": "existing"}')
        self.assertEqual(session.read_text(), "preserve")
        self.assertIn(f"{self.shared_home()}:/home/codex:rw", self.info()["args"])

    def test_wrapper_profile_environment_is_ignored(self):
        for profile in ("vet-2", "../other"):
            result = self.run_wrapper(env={**self.env, "CODEX_PRO_PROFILE": profile})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"{self.shared_home()}:/home/codex:rw", self.info()["args"])
        self.assertFalse((self.shared_home() / "projects").exists())

    def test_regex_snapshots_all_matches_and_no_parents(self):
        inputs = self.root / "inputs with spaces"
        inputs.mkdir()
        first = inputs / "tmp-one.diff"
        second = inputs / "tmp-two.diff"
        first.write_text("first review")
        second.write_text("second review")
        (inputs / "unrelated.txt").write_text("private")
        (self.install / ".files").write_text(f"ro-regex\t{inputs}\ttmp[^/]+\\.diff\n")
        output = self.root / "answer with spaces.txt"
        result = self.run_wrapper(["exec", "--output-last-message", str(output), "-"])
        self.assertEqual(result.returncode, 0, result.stderr)
        info = self.info()
        self.assertEqual(info["snapshots"], {str(first): "first review", str(second): "second review"})
        self.assertFalse(any(arg.startswith(str(inputs) + ":") for arg in info["args"]))
        for arg in info["args"]:
            if "/input-" in arg:
                self.assertFalse(Path(arg.split(":")[0]).exists())
        self.assertIn("Complete review", output.read_text())

    def test_snapshot_survives_original_file_changes(self):
        source = self.root / "review.diff"
        source.write_text("original input")
        (self.install / ".files").write_text(f"ro-regex\t{self.root}\treview[.]diff\n")
        process = self.start_blocked()
        source.write_text("changed after the invocation started")
        name = (self.state / "blocked").read_text()
        pid = int((self.state / (name + ".pid")).read_text())
        os.kill(pid, signal.SIGTERM)
        _, errors = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, errors)
        self.assertEqual(self.info()["snapshots"], {str(source): "original input"})
        self.assertEqual(source.read_text(), "changed after the invocation started")
        self.assert_no_containers()

    def test_temp_regex_and_zero_matches(self):
        with tempfile.NamedTemporaryFile(suffix=".diff", mode="w", delete=True) as source:
            source.write("full diff\n")
            source.flush()
            (self.install / ".files").write_text(f"ro-regex\t{Path(source.name).parent}\t{re_escape(Path(source.name).name)}\n")
            result = self.run_wrapper()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.info()["snapshots"][source.name], "full diff\n")
        (self.install / ".files").write_text(f"ro-regex\t{self.root}\tDOES-NOT-EXIST\n")
        self.assertEqual(self.run_wrapper().returncode, 0)

    def test_rules_reject_symlinks_socket_invalid_and_broad_paths(self):
        source = self.root / "source.diff"
        source.write_text("input")
        link = self.root / "link.diff"
        link.symlink_to(source)
        sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(sock.close)
        sock.bind(str(self.root / "socket.diff"))
        configs = ["rw\t/tmp\n", "ro\t/\n", f"ro\t{self.home}\n",
                   "ro\t/run\n", f"ro\t{link}\n", "ro /tmp\n",
                   f"ro-regex\t{self.root}\t[\n", f"ro-regex\t{self.root}\tlink.*\n",
                   f"ro-regex\t{self.root}\tsocket.*\n",
                   f"ro\t{source}\nrw\t{source}\n"]
        for content in configs:
            with self.subTest(content=content):
                (self.install / ".files").write_text(content)
                result = self.run_wrapper()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(".files", result.stderr)
        self.assertFalse(self.state.exists())

    def test_static_mount_and_deduplication(self):
        source = self.root / "reference with spaces.txt"
        source.write_text("reference")
        (self.install / ".files").write_text(f"ro\t{source}\nro\t{source}\n")
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.info()["args"].count(f"{source}:{source}:ro"), 1)
        creates = [call for call in self.calls() if call[0] == "create"]
        self.assertNotIn(f"{source}:{source}:ro", creates[0])

    def test_output_spellings_and_relative_cd(self):
        subdir = self.project / "subdir"
        subdir.mkdir()
        for option in (["--output-last-message", "answer.txt"],
                       ["--output-last-message=answer.txt"], ["-o", "answer.txt"], ["-o=answer.txt"]):
            with self.subTest(option=option):
                result = self.run_wrapper(["exec", "--cd", str(subdir), *option, "-"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Complete review", (subdir / "answer.txt").read_text())
                (subdir / "answer.txt").unlink()

    def test_output_failure_and_symlink_no_stale_success(self):
        output = self.root / "output"
        output.write_text("old result")
        for failure in ("exec", "missing-output"):
            result = self.run_wrapper(["exec", "-o", str(output), "-"],
                                      env={**self.env, "FAKE_FAIL": failure})
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), "old result")
            self.assert_no_containers()
        link = self.root / "output-link"
        link.symlink_to(output)
        self.assertNotEqual(self.run_wrapper(["exec", "-o", str(link), "-"]).returncode, 0)

    def test_output_input_conflict_fails_before_starting_containers(self):
        output = self.root / "output.diff"
        output.write_text("unchanged")
        (self.install / ".files").write_text(f"ro\t{output}\n")
        result = self.run_wrapper(["exec", "-o", str(output), "-"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts with a .files input", result.stderr)
        self.assertFalse(self.state.exists())
        self.assertEqual(output.read_text(), "unchanged")

    def test_concurrent_sessions_share_home_across_commands_and_projects(self):
        for stage in ("update", "exec"):
            with self.subTest(stage=stage):
                (self.state / "blocked").unlink(missing_ok=True)
                process = self.start_blocked(stage)
                record = self.recovery_record()
                original = record.read_text()
                self.assertFalse(self.shared_home() in record.parents)
                other = self.root / f"other-project-{stage}"
                other.mkdir()
                for cwd in (self.project, other):
                    result = self.run_wrapper(cwd=cwd)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    result = self.run_wrapper(["--help"], cwd=cwd)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIsNone(process.poll())
                    self.assertEqual(record.read_text(), original)
                    self.assertIn(f"{self.shared_home()}:/home/codex:rw", self.info()["args"])
                process.terminate()
                process.communicate(timeout=10)
                self.assert_no_containers()
                self.assertFalse(record.exists())

    def test_sigkill_parent_cleans_update_and_exec(self):
        for stage in ("update", "exec"):
            with self.subTest(stage=stage):
                (self.state / "blocked").unlink(missing_ok=True)
                process = self.start_blocked(stage)
                process.kill()
                process.communicate(timeout=10)
                self.assert_no_containers()
                self.assertEqual(self.run_wrapper().returncode, 0)

    def test_failures_allow_subsequent_runs(self):
        for failure in ("build", "create", "update"):
            with self.subTest(failure=failure):
                (self.state / "image").unlink(missing_ok=True)
                result = self.run_wrapper(env={**self.env, "FAKE_FAIL": failure})
                self.assertNotEqual(result.returncode, 0)
                self.assert_no_containers()
                self.assertEqual(self.run_wrapper().returncode, 0)

    def test_subprocess_timeout_cleans_container_and_exchange(self):
        output = self.root / "timed-out-answer"
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run([str(self.install / "codex"), "exec", "-o", str(output), "-"],
                           cwd=self.project, env={**self.env, "FAKE_BLOCK": "exec"},
                           input="review", text=True, capture_output=True, timeout=2)
        self.assertTrue((self.state / "blocked").exists())
        # On POSIX subprocess.run waits for the killed launcher, not its worker.
        # Originals can disappear immediately; the worker owns its snapshots and
        # pinned output FD until asynchronous cleanup finishes.
        output.unlink()
        deadline = time.monotonic() + 8
        while list(self.state.glob("codex-pro-*.json")) or list(self.state.glob("*.pid")):
            if time.monotonic() > deadline:
                self.fail("Timeout cleanup did not finish")
            time.sleep(0.02)
        self.assert_no_containers()
        self.assertFalse(output.exists())
        calls = self.calls()
        execution = next(call for call in calls if call[0] == "create" and "-i" in call)
        exchange = next(Path(arg.split(":")[0]) for arg in execution if ":/codex-exchange:" in arg)
        while exchange.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(exchange.exists())
        self.assertEqual(self.run_wrapper().returncode, 0)

    def test_checkout_cannot_expose_wrapper_state(self):
        result = self.run_wrapper(env={**self.env, "CODEX_PRO_HOME": str(self.project / "state")})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not contain", result.stderr)
        self.assertFalse((self.project / "state").exists())

    def test_cleanup_failure_preserves_record_without_blocking_shared_home(self):
        result = self.run_wrapper(env={**self.env, "FAKE_FAIL": "cleanup"})
        self.assertNotEqual(result.returncode, 0)
        record = self.recovery_record()
        self.assertTrue(record.is_file())
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(record.is_file())
        recovery = json.loads(record.read_text())
        for name in recovery["containers"]:
            subprocess.run([str(self.root / "bin/podman"), "rm", "--force", "--ignore", name],
                           env=self.env, check=True)
        shutil.rmtree(recovery["exchange"])
        record.unlink()
        self.assertEqual(self.run_wrapper().returncode, 0)

    def test_sqlite_override_rejected_and_package_validation(self):
        for option in (["-c", 'sqlite_home="/tmp/state"'], ["--config=sqlite_home='other'"]):
            self.assertNotEqual(self.run_wrapper([*option, "exec", "-"]).returncode, 0)
        (self.install / ".packages").write_text("curl;echo secret\n")
        result = self.run_wrapper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid package", result.stderr)

    @unittest.skipUnless(os.environ.get("CODEX_VET_HELPER"), "Set CODEX_VET_HELPER to the PR #1880 script")
    def test_unchanged_vet_helper_single_batches_synthesis_and_exemption(self):
        helper_path = Path(os.environ["CODEX_VET_HELPER"])
        helper_spec = importlib.util.spec_from_file_location("upstream_vet_helper", helper_path)
        helper = importlib.util.module_from_spec(helper_spec)
        with mock.patch.dict(sys.modules, {"upstream_vet_helper": helper}):
            helper_spec.loader.exec_module(helper)
        helper.PROJECT_ROOT = self.project
        (self.install / ".files").write_text("ro-regex\t/tmp\ttmp[^/]+\\.diff\n")
        analyzer = helper.AIAnalyzer("codex", path=str(self.install / "codex"))
        kwargs = {"crate_name": "example", "old_version": "1.0.0", "new_version": "1.0.1",
                  "publisher": "example"}
        small = "diff --git a/lib.rs b/lib.rs\n+fn example() {}\n"
        large = "diff --git a/lib.rs b/lib.rs\n" + "+let x = 1;\n" * 1600
        with mock.patch.dict(os.environ, {**self.env, "FAKE_VET": "1"}), \
                mock.patch.object(tempfile, "tempdir", "/tmp"):
            single = analyzer.analyze_diff(small, **kwargs)
            batched = analyzer.analyze_diff(large, **kwargs)
            exemption = analyzer.analyze_diff(batched, _exemption_note=True, **kwargs)
        self.assertIn("Complete review", single)
        self.assertIn("2/2 batches complete", batched)
        self.assertIn("Synthesis complete", batched)
        self.assertIn("Exemption, not certification", exemption)
        reviews = [json.loads(line) for line in (self.state / "reviews.jsonl").read_text().splitlines()]
        self.assertEqual(len(reviews), 5)
        inputs = [[content for content in review["inputs"].values()] for review in reviews]
        self.assertIn(small, inputs[0])
        # Read files through the fake container boundary, not by inspecting
        # analyzer internals: all supplied batch content must have arrived.
        self.assertEqual(inputs[1][0] + inputs[2][0], large)
        self.assertIn("Batch 1/2", inputs[3][0])
        self.assertIn(batched, inputs[4])
        for review in reviews:
            for path in review["inputs"]:
                self.assertFalse(Path(path).exists())
        self.assert_no_containers()


def re_escape(value):
    import re
    return re.escape(value)


if __name__ == "__main__":
    unittest.main()
