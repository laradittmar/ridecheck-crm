"""PRELAUNCH-MINIMUM-LAUNCH-DEPLOYMENT-PREPARATION-R3, Objective C: the rollback-compatibility
candidate is 198ed4d plus ONE accepted-schema literal.

These tests pin that claim. They are the reason the inherited head-pin tests are not edited:
`test_convergence_p4_strict_schema` and `test_w1f_w01_r2_outstanding_question::test_j12` compare
EXPECTED_HEAD with this tree's script directory,
which deliberately keeps 198ed4d's migrations, so those two assertions are expected to fail here.
The migration is never run from this image; the final image owns it.
"""
import ast
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PARENT = "198ed4d6a7157230a616063094e2d50fb9623836"
PREFLIGHT = "backend/app/services/schema_preflight.py"
NEW_HEAD = "20261104_vehicle_identity"
OLD_HEAD = "20261103_slot_recovery_job"


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True,
                          text=True).stdout


def _without_head(source):
    """The module's AST with the EXPECTED_HEAD value blanked (comments are not in an AST)."""
    tree = ast.parse(source)
    found = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "EXPECTED_HEAD":
            found.append(node.value.value)
            node.value.value = "<HEAD>"
    return ast.dump(tree), found


class RollbackCompatIsOneLiteral(unittest.TestCase):
    def test_only_the_preflight_differs_from_the_parent_under_backend(self):
        changed = _git("diff", "--name-only", PARENT, "--", "backend").split()
        self.assertEqual(changed, [PREFLIGHT])

    def test_no_untracked_file_under_backend(self):
        self.assertEqual(_git("ls-files", "--others", "--exclude-standard", "--", "backend"), "")

    def test_the_preflight_differs_only_by_the_head_literal(self):
        parent, parent_head = _without_head(_git("show", f"{PARENT}:{PREFLIGHT}"))
        current, current_head = _without_head((ROOT / PREFLIGHT).read_text(encoding="utf-8"))
        self.assertEqual(parent_head, [OLD_HEAD])
        self.assertEqual(current_head, [NEW_HEAD])
        self.assertEqual(parent, current)

    def test_the_comparison_stays_exact(self):
        """Schema verification is not weakened: one exact head, still an equality."""
        source = (ROOT / PREFLIGHT).read_text(encoding="utf-8")
        self.assertIn("if heads != [EXPECTED_HEAD]:", source)
        self.assertNotIn(OLD_HEAD + '"', source.split("EXPECTED_HEAD = ")[1].split("\n")[0])


class RollbackCompatAddsNoMinimumLaunchFunctionality(unittest.TestCase):
    def test_no_vehicle_identity_code(self):
        self.assertFalse((ROOT / "backend/app/services/vehicle_identity.py").exists())
        models = (ROOT / "backend/app/models.py").read_text(encoding="utf-8")
        self.assertNotIn("kilometraje", models)
        self.assertNotIn("dominio", models)

    def test_no_prefill_switch(self):
        settings = (ROOT / "backend/app/settings.py").read_text(encoding="utf-8")
        self.assertNotIn("BOOKING_FLOW_DETAILS_PREFILL_ENABLED", settings)

    def test_the_migration_is_not_carried(self):
        self.assertFalse((ROOT / "backend/migrations/versions/20261104_vehicle_identity.py").exists())


if __name__ == "__main__":
    unittest.main()
