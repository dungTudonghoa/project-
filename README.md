# Supply chain inventory ledger

Ứng dụng dòng lệnh Python để ghi nhận tồn kho hàng tiêu dùng, đơn hàng và
báo cáo nhu cầu theo mã hàng (SKU). Dữ liệu được lưu trong tệp JSON trên máy.

**Phạm vi:** đây là công cụ quản lý dữ liệu độc lập, không phải bản tái hiện
bài báo arXiv:2608.10245 và không có mô hình GNN–GA hay bộ tối ưu phân phối.

## Yêu cầu và chạy

Python 3.11 trở lên; không cần thư viện bên ngoài.

```bash
python inventory_ledger.py --help
python -m unittest -v
```

Ví dụ thao tác với một tệp ledger:

```bash
python inventory_ledger.py init ledger.json
python inventory_ledger.py receive ledger.json apples 10 --receipt-id r-001
python inventory_ledger.py receive ledger.json bananas 6 --receipt-id r-002
python inventory_ledger.py order ledger.json o-001 apples 3
python inventory_ledger.py order ledger.json o-002 bananas 2
python inventory_ledger.py report ledger.json
```

Lệnh `receive` cũng chấp nhận dạng đầy đủ `RECEIPT_ID SKU QUANTITY`, hoặc
dạng cờ `--receipt-id`, `--sku`, `--quantity`. Lệnh `order` chấp nhận dạng
cờ tương ứng `--order-id`, `--sku`, `--quantity`.

## Quy ước nghiệp vụ

- Nhập kho làm tăng số lượng hàng đã nhận theo SKU.
- Đơn hàng ghi nhận nhu cầu đang mở; ghi đơn không trừ tồn kho.
- Báo cáo tổng hợp tồn kho và nhu cầu để người dùng đối chiếu.
- Số lượng phải là số nguyên dương, mã hàng và mã đơn không được rỗng.
- Mã đơn trùng bị từ chối để tránh ghi nhận hai lần cùng một đơn.

Đây là bản khởi đầu dành cho một người dùng, thao tác tuần tự trên một tệp.
Không chạy nhiều tiến trình ghi cùng tệp. Chưa có quy trình xuất kho,
hủy/hoàn tất đơn hàng, phân quyền hay đồng bộ nhiều người dùng; số liệu
đơn hàng tiếp tục là nhu cầu đang mở cho đến khi bổ sung quy trình đó.
Tệp JSON được ghi theo cách thay thế nguyên tử để tránh lưu dở dang.

GitHub Actions chạy bộ kiểm thử Python khi push hoặc mở pull request.
