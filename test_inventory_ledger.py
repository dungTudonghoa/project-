import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from inventory_ledger import (
    DuplicateIDError,
    InventoryLedger,
    LedgerError,
)


class InventoryLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.path = self.root / "ledger.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_init_is_json_and_never_silently_overwrites(self):
        ledger = InventoryLedger.create(self.path)
        self.assertEqual(ledger.report()["stock_by_sku"], {})
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")),
            {"version": 1, "receipts": [], "orders": []},
        )

        original = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            InventoryLedger.create(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_receipts_orders_and_report_persist(self):
        InventoryLedger.create(self.path)
        ledger = InventoryLedger.load(self.path)
        ledger.receive(" apples ", 10, "r-001", received_at="2026-01-01T00:00:00Z")
        ledger.receive("apples", 2, "r-002", received_at="2026-01-02T00:00:00Z")
        ledger.add_order("o-001", "apples", 7, ordered_at="2026-01-03T00:00:00Z")
        ledger.add_order("o-002", "oranges", 4, ordered_at="2026-01-04T00:00:00Z")

        report = ledger.report()
        self.assertEqual(report["stock_by_sku"], {"apples": 12})
        self.assertEqual(report["demand_by_sku"], {"apples": 7, "oranges": 4})
        self.assertEqual(
            report["by_sku"],
            {
                "apples": {"stock": 12, "requested": 7},
                "oranges": {"stock": 0, "requested": 4},
            },
        )
        self.assertTrue(report["orders_are_open_demand"])

        # Saving and loading retains quantities, and orders do not consume stock.
        ledger.save(self.path)
        reloaded = InventoryLedger.load(self.path)
        self.assertEqual(reloaded.report(), report)
        self.assertEqual(reloaded.stock_by_sku(), {"apples": 12})

    def test_quantities_and_identifiers_are_validated(self):
        ledger = InventoryLedger.empty()
        for quantity in (0, -1, True, False, 1.5, "2"):
            with self.subTest(quantity=quantity):
                with self.assertRaises(LedgerError):
                    ledger.receive("apples", quantity, f"r-{quantity!r}")
        for sku in ("", "   ", None, 5):
            with self.subTest(sku=sku):
                with self.assertRaises(LedgerError):
                    ledger.receive(sku, 1, f"r-{sku!r}")
        with self.assertRaises(LedgerError):
            ledger.add_order("   ", "apples", 1)

    def test_duplicate_receipt_and_order_ids_are_rejected(self):
        ledger = InventoryLedger.empty()
        ledger.receive("apples", 3, "r-001", received_at="t1")
        with self.assertRaises(DuplicateIDError):
            ledger.receive("oranges", 9, "r-001", received_at="t2")
        self.assertEqual(ledger.stock_by_sku(), {"apples": 3})

        ledger.add_order("o-001", "apples", 2, ordered_at="t1")
        with self.assertRaises(DuplicateIDError):
            ledger.add_order("o-001", "apples", 99, ordered_at="t2")
        self.assertEqual(ledger.demand_by_sku(), {"apples": 2})

    def test_save_replaces_atomically_and_leaves_no_temp_files(self):
        InventoryLedger.create(self.path)
        ledger = InventoryLedger.load(self.path)
        ledger.receive("rice", 5, "r-001", received_at="fixed")
        ledger.save(self.path)

        self.assertEqual(InventoryLedger.load(self.path).stock_by_sku(), {"rice": 5})
        self.assertEqual(
            list(self.root.glob(f".{self.path.name}.*.tmp")),
            [],
        )
        # A complete JSON document is visible after the replacement.
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["version"], 1)

    def run_cli(self, *args):
        script = Path(__file__).with_name("inventory_ledger.py")
        return subprocess.run(
            [sys.executable, str(script), *map(str, args)],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_cli_commands_cover_init_receive_order_report(self):
        result = self.run_cli("init", self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["initialized"], True)

        result = self.run_cli(
            "receive", self.path, "apples", "10", "--receipt-id", "r-001"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["quantity"], 10)

        result = self.run_cli("order", self.path, "o-001", "apples", "3")
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_cli("report", self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["stock_by_sku"], {"apples": 10})
        self.assertEqual(report["demand_by_sku"], {"apples": 3})

        original = self.path.read_bytes()
        result = self.run_cli("init", self.path)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.path.read_bytes(), original)

    def test_cli_rejects_invalid_positional_quantity_without_traceback(self):
        self.assertEqual(self.run_cli("init", self.path).returncode, 0)
        result = self.run_cli("receive", self.path, "apples", "zero")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(InventoryLedger.load(self.path).report()["stock_by_sku"], {})


if __name__ == "__main__":
    unittest.main()
