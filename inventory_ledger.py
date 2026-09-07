"""Small, file-backed grocery inventory and order ledger.

The ledger records received grocery stock and requested grocery orders.  Orders
remain open demand and never reduce stock.  It deliberately does not allocate,
fulfil, optimize, route, or make any other operational decisions.

CLI examples::

    python inventory_ledger.py init ledger.json
    python inventory_ledger.py receive ledger.json apples 10 --receipt-id r-001
    python inventory_ledger.py order ledger.json o-001 apples 3
    python inventory_ledger.py report ledger.json

The JSON file is replaced atomically on each mutation.  This is intended for a
single writer; callers that coordinate concurrent writers should provide their
own locking.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from typing import Any, Mapping

SCHEMA_VERSION = 1


class LedgerError(ValueError):
    """Base class for invalid ledger operations or data."""


class DuplicateIDError(LedgerError):
    """Raised when an operation would reuse an existing identifier."""


class LedgerFormatError(LedgerError):
    """Raised when a JSON file does not have the ledger schema."""


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise LedgerError(f"{field} must be a nonempty identifier")
    value = value.strip()
    if not value:
        raise LedgerError(f"{field} must be a nonempty identifier")
    return value


def _positive_integer(value: Any, field: str = "quantity") -> int:
    # bool is an int subclass, but True/False are not quantities.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LedgerError(f"{field} must be a positive integer")
    return value


def _timestamp(value: Any, field: str) -> str:
    if value is None:
        return _datetime.datetime.now(_datetime.timezone.utc).isoformat()
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"{field} must be a nonempty string")
    return value.strip()


def _record(record: Mapping[str, Any], kind: str) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise LedgerFormatError(f"each {kind} must be an object")
    id_field = f"{kind[:-1]}_id" if kind.endswith("s") else "id"
    # Keep the two record types explicit in the schema rather than allowing
    # arbitrary dictionaries to be silently persisted.
    required = (id_field, "sku", "quantity")
    for field in required:
        if field not in record:
            raise LedgerFormatError(f"each {kind} record requires {field}")
    result = {
        id_field: _identifier(record[id_field], id_field),
        "sku": _identifier(record["sku"], "sku"),
        "quantity": _positive_integer(record["quantity"]),
    }
    time_field = "received_at" if kind == "receipts" else "ordered_at"
    if time_field in record and record[time_field] is not None:
        result[time_field] = _timestamp(record[time_field], time_field)
    else:
        # Older/minimal files may omit timestamps.  Loading remains lossless
        # for the fields they contain, and new records always get one.
        result[time_field] = None
    return result


def _empty_document() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "receipts": [], "orders": []}


def _validate_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise LedgerFormatError("ledger JSON must contain an object")
    if document.get("version") != SCHEMA_VERSION:
        raise LedgerFormatError(
            f"unsupported ledger version {document.get('version')!r}; expected {SCHEMA_VERSION}"
        )
    receipts = document.get("receipts")
    orders = document.get("orders")
    if not isinstance(receipts, list) or not isinstance(orders, list):
        raise LedgerFormatError("ledger requires receipts and orders arrays")

    normalized: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "receipts": [],
        "orders": [],
    }
    seen_receipts: set[str] = set()
    seen_orders: set[str] = set()
    for raw in receipts:
        item = _record(raw, "receipts")
        receipt_id = item["receipt_id"]
        if receipt_id in seen_receipts:
            raise LedgerFormatError(f"duplicate receipt_id {receipt_id!r}")
        seen_receipts.add(receipt_id)
        normalized["receipts"].append(item)
    for raw in orders:
        item = _record(raw, "orders")
        order_id = item["order_id"]
        if order_id in seen_orders:
            raise LedgerFormatError(f"duplicate order_id {order_id!r}")
        seen_orders.add(order_id)
        normalized["orders"].append(item)
    return normalized


def _atomic_write(path: Path, text: str, *, overwrite: bool) -> None:
    """Publish *text* as *path* without exposing a partially written file."""
    parent = path.parent
    if not parent.exists():
        raise FileNotFoundError(f"parent directory does not exist: {parent}")
    if not parent.is_dir():
        raise NotADirectoryError(str(parent))

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary_path, path)
        else:
            # Hard-linking a complete temporary file is an atomic create and
            # fails if another writer created the destination first.
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                raise FileExistsError(f"ledger already exists: {path}") from None
            finally:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
        # Best-effort directory durability on platforms that expose it.
        try:
            directory_fd = os.open(parent, os.O_RDONLY)
        except (OSError, TypeError):
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        # os.replace and the link path both consume/unlink the temporary name;
        # this also cleans up errors before publication.
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


class InventoryLedger:
    """In-memory grocery receipts and orders with JSON persistence."""

    def __init__(self, document: Mapping[str, Any] | None = None) -> None:
        self._document = _validate_document(
            _empty_document() if document is None else document
        )

    @classmethod
    def empty(cls) -> "InventoryLedger":
        return cls()

    @classmethod
    def create(cls, path: str | os.PathLike[str]) -> "InventoryLedger":
        """Create and atomically initialize a new ledger file."""
        target = Path(path)
        # _atomic_write with overwrite=False is the race-safe no-overwrite
        # check; this early check gives a useful error for an existing folder.
        if target.exists():
            raise FileExistsError(f"ledger already exists: {target}")
        ledger = cls.empty()
        ledger._write(target, overwrite=False)
        return ledger

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "InventoryLedger":
        target = Path(path)
        try:
            with target.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
        except FileNotFoundError:
            raise
        except json.JSONDecodeError as exc:
            raise LedgerFormatError(f"invalid ledger JSON: {exc}") from exc
        except OSError:
            raise
        return cls(document)

    def _write(self, path: Path, *, overwrite: bool) -> None:
        text = json.dumps(
            self._document, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        _atomic_write(path, text, overwrite=overwrite)

    def save(self, path: str | os.PathLike[str]) -> None:
        """Atomically replace an existing ledger file with current contents."""
        self._write(Path(path), overwrite=True)

    def receive(
        self,
        sku: str,
        quantity: int,
        receipt_id: str,
        received_at: str | None = None,
    ) -> dict[str, Any]:
        """Record a receipt; stock increases only through this operation."""
        sku = _identifier(sku, "sku")
        receipt_id = _identifier(receipt_id, "receipt_id")
        quantity = _positive_integer(quantity)
        received_at = _timestamp(received_at, "received_at")
        if any(item["receipt_id"] == receipt_id for item in self._document["receipts"]):
            raise DuplicateIDError(f"receipt_id already exists: {receipt_id}")
        item = {
            "receipt_id": receipt_id,
            "sku": sku,
            "quantity": quantity,
            "received_at": received_at,
        }
        self._document["receipts"].append(item)
        return dict(item)

    def add_order(
        self,
        order_id: str,
        sku: str,
        quantity: int,
        ordered_at: str | None = None,
    ) -> dict[str, Any]:
        """Record requested quantity as open demand; stock is unchanged."""
        order_id = _identifier(order_id, "order_id")
        sku = _identifier(sku, "sku")
        quantity = _positive_integer(quantity)
        ordered_at = _timestamp(ordered_at, "ordered_at")
        if any(item["order_id"] == order_id for item in self._document["orders"]):
            raise DuplicateIDError(f"order_id already exists: {order_id}")
        item = {
            "order_id": order_id,
            "sku": sku,
            "quantity": quantity,
            "ordered_at": ordered_at,
        }
        self._document["orders"].append(item)
        return dict(item)

    # A short verb is convenient for callers and mirrors the CLI command.
    order = add_order

    @property
    def receipts(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._document["receipts"])

    @property
    def orders(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._document["orders"])

    def stock_by_sku(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for item in self._document["receipts"]:
            sku = item["sku"]
            totals[sku] = totals.get(sku, 0) + item["quantity"]
        return dict(sorted(totals.items()))

    def demand_by_sku(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for item in self._document["orders"]:
            sku = item["sku"]
            totals[sku] = totals.get(sku, 0) + item["quantity"]
        return dict(sorted(totals.items()))

    def report(self) -> dict[str, Any]:
        """Return stock and requested quantities without allocating stock."""
        stock = self.stock_by_sku()
        demand = self.demand_by_sku()
        skus = sorted(set(stock) | set(demand))
        return {
            "stock_by_sku": stock,
            "demand_by_sku": demand,
            "by_sku": {
                sku: {"stock": stock.get(sku, 0), "requested": demand.get(sku, 0)}
                for sku in skus
            },
            "orders_are_open_demand": True,
        }


def _positive_integer_arg(value: str) -> int:
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if integer <= 0 or str(integer) != value.strip():
        raise argparse.ArgumentTypeError("must be a positive integer")
    return integer


def _nonempty_arg(value: str) -> str:
    try:
        return _identifier(value, "identifier")
    except LedgerError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a new empty ledger")
    init_parser.add_argument("path", type=Path)

    receive_parser = subparsers.add_parser("receive", help="record received stock")
    receive_parser.add_argument("path", type=Path)
    receive_parser.add_argument("values", nargs="*", metavar="VALUE")
    receive_parser.add_argument("--receipt-id", dest="receipt_id")
    receive_parser.add_argument("--sku")
    receive_parser.add_argument("--quantity", type=_positive_integer_arg)
    receive_parser.add_argument("--received-at")

    order_parser = subparsers.add_parser("order", help="record open demand")
    order_parser.add_argument("path", type=Path)
    order_parser.add_argument("values", nargs="*", metavar="VALUE")
    order_parser.add_argument("--order-id", dest="order_id")
    order_parser.add_argument("--sku")
    order_parser.add_argument("--quantity", type=_positive_integer_arg)
    order_parser.add_argument("--ordered-at")

    report_parser = subparsers.add_parser("report", help="report stock and open demand")
    report_parser.add_argument("path", type=Path)
    return parser


def _resolve_receive(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict[str, Any]:
    values = args.values
    if values:
        if len(values) == 2:
            positional = {"sku": values[0], "quantity": values[1]}
        elif len(values) == 3:
            positional = {
                "receipt_id": values[0],
                "sku": values[1],
                "quantity": values[2],
            }
        else:
            parser.error("receive accepts SKU QUANTITY or RECEIPT_ID SKU QUANTITY")
        for key, value in positional.items():
            if getattr(args, key) is not None:
                parser.error(f"{key.replace('_', '-')} supplied twice")
            setattr(args, key, value)
    if args.sku is None or args.quantity is None:
        parser.error("receive requires SKU and QUANTITY")
    quantity = args.quantity
    receipt_id = (
        f"receipt-{uuid.uuid4().hex}"
        if args.receipt_id is None
        else args.receipt_id
    )
    try:
        quantity = _positive_integer_arg(str(quantity))
    except argparse.ArgumentTypeError as exc:
        raise LedgerError(str(exc)) from exc
    return {
        "receipt_id": _identifier(receipt_id, "receipt_id"),
        "sku": _identifier(args.sku, "sku"),
        "quantity": quantity,
        "received_at": args.received_at,
    }


def _resolve_order(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict[str, Any]:
    values = args.values
    if values:
        if len(values) != 3:
            parser.error("order accepts ORDER_ID SKU QUANTITY")
        positional = {
            "order_id": values[0],
            "sku": values[1],
            "quantity": values[2],
        }
        for key, value in positional.items():
            if getattr(args, key) is not None:
                parser.error(f"{key.replace('_', '-')} supplied twice")
            setattr(args, key, value)
    if args.order_id is None or args.sku is None or args.quantity is None:
        parser.error("order requires ORDER_ID, SKU, and QUANTITY")
    quantity = args.quantity
    if not isinstance(quantity, int):
        try:
            quantity = _positive_integer_arg(str(quantity))
        except argparse.ArgumentTypeError as exc:
            raise LedgerError(str(exc)) from exc
    else:
        quantity = _positive_integer(quantity)
    return {
        "order_id": _identifier(args.order_id, "order_id"),
        "sku": _identifier(args.sku, "sku"),
        "quantity": _positive_integer(quantity),
        "ordered_at": args.ordered_at,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            InventoryLedger.create(args.path)
            print(json.dumps({"path": str(args.path), "initialized": True}, sort_keys=True))
            return 0
        if args.command == "report":
            print(json.dumps(InventoryLedger.load(args.path).report(), indent=2, sort_keys=True))
            return 0
        if args.command == "receive":
            values = _resolve_receive(args, parser)
            ledger = InventoryLedger.load(args.path)
            item = ledger.receive(**values)
            ledger.save(args.path)
            print(json.dumps(item, indent=2, sort_keys=True))
            return 0
        if args.command == "order":
            values = _resolve_order(args, parser)
            ledger = InventoryLedger.load(args.path)
            item = ledger.add_order(**values)
            ledger.save(args.path)
            print(json.dumps(item, indent=2, sort_keys=True))
            return 0
        parser.error(f"unknown command: {args.command}")
    except (LedgerError, FileExistsError, FileNotFoundError, NotADirectoryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
