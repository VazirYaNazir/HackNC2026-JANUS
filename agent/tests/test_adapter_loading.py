"""Only local configuration can select an existing audited worker implementation."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.isolation import IsolationBoundary, load_boundary
from tests.support import SimulatedBoundary


class AdapterLoadingTests(unittest.TestCase):
    def test_default_stays_blocked_without_importing_a_worker(self):
        with patch("agent.isolation.importlib.import_module") as imported:
            self.assertFalse(load_boundary(None).can_build())
            imported.assert_not_called()

    def test_explicit_local_factory_reuses_existing_async_interface(self):
        boundary = SimulatedBoundary()
        with patch("agent.isolation.importlib.import_module", return_value=SimpleNamespace(factory=lambda: boundary)) as imported:
            self.assertIs(load_boundary("local_app.worker:factory"), boundary)
            imported.assert_called_once_with("local_app.worker")

    def test_cloud_style_paths_expressions_and_malformed_names_never_import(self):
        for value in ("/tmp/worker.py:factory", "worker", "worker:factory()", "worker:factory.extra", "../worker:factory", 1):
            with self.subTest(value=value), patch("agent.isolation.importlib.import_module") as imported, self.assertRaises(ValueError):
                load_boundary(value)
            imported.assert_not_called()

    def test_missing_module_factory_or_worker_guarantees_fail_closed_and_hide_details(self):
        for value in (SimpleNamespace(), SimpleNamespace(factory=lambda: object()), SimpleNamespace(factory=lambda: IsolationBoundary())):
            with self.subTest(module=value), patch("agent.isolation.importlib.import_module", return_value=value), self.assertRaisesRegex(ValueError, "^Cannot load a conforming local isolation adapter$"):
                load_boundary("local_app:factory")
        with patch("agent.isolation.importlib.import_module", side_effect=ImportError("private credential detail")), self.assertRaisesRegex(ValueError, "^Cannot load a conforming local isolation adapter$"):
            load_boundary("local_app:factory")

    def test_synchronous_worker_methods_are_rejected_before_connecting(self):
        class Blocking(SimulatedBoundary):
            def compile(self, *args):
                return None
        with patch("agent.isolation.importlib.import_module", return_value=SimpleNamespace(factory=Blocking)), self.assertRaises(ValueError):
            load_boundary("local_app:factory")
