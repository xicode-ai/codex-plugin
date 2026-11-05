#!/usr/bin/env python3
"""Create synthetic, intentionally incomplete source repositories for isolated evaluations."""

import argparse
from pathlib import Path


FILES = {
    'orders/service.py': '''# 业务状态说明：NEW=新建，REVIEW=审核中，APPROVED=审核通过，CANCELLED=已取消。
# DISPATCH_REQUESTED=已提交仓库申请；DELIVERED 在本系统只表示交仓完成。
AUTO_DISPATCH = False

def cancel(order):
    if order.status == "REVIEW":
        raise ValueError("审核中不能取消")
    if order.status not in {"NEW", "APPROVED"}:
        raise ValueError("当前状态不能取消")
    order.status = "CANCELLED"
    return order

def approve(order, bus):
    order.status = "APPROVED"
    if AUTO_DISPATCH:
        bus.publish("shipment.requested.v2", {"order_id": order.id})

def dispatch(order, bus):
    if order.status != "APPROVED":
        raise ValueError("先审核")
    bus.publish("shipment.requested.v2", {"order_id": order.id})
    order.status = "DISPATCH_REQUESTED"

def warehouse_handoff_callback(order):
    order.status = "DELIVERED"  # 交仓完成，不代表承运商送达
''',
    'orders/routes.py': '''from service import cancel, approve, dispatch, warehouse_handoff_callback
ROUTES = {"POST /orders/cancel": cancel, "POST /orders/approve": approve,
          "POST /orders/dispatch": dispatch, "POST /orders/handoff": warehouse_handoff_callback}
''',
    'orders/legacy.py': '''def auto_refund(order, payments):
    payments.refund(order.total)
    order.status = "REFUNDED"
''',
    'warehouse/consumer.py': '''TOPIC = "shipment.requested.v1"
def consume(payload, warehouse):
    return warehouse.create_pick_task(payload["order_no"])
''',
    'warehouse/schema.sql': 'CREATE TABLE pick_task (id BIGINT PRIMARY KEY, order_no VARCHAR(64), status VARCHAR(32));\n',
    'legacy-product-notes.md': '# 旧版本计划（未验收）\n订单审核后自动分配仓库；取消订单自动退款；DELIVERED 表示客户签收。\n',
    'request.md': '请分析 orders 与 warehouse 两个仓库，生成产品经理、运营及业务问答可阅读的知识文档，有业务小节和解释段落，知识正文不含代码、实现细节或源码引用。已有一份未验收的旧产品说明，没有数据库和前端；证据与问题另存 analysis/，所有产物写入指定输出目录，不改输入、不调用外部服务或导入知识库。\n',
}


def create_case(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in FILES.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    for name in ('baseline-output', 'skill-output'):
        (destination / name).mkdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='new directory; existing directories are never overwritten')
    args = parser.parse_args()
    try:
        create_case(args.destination)
    except OSError:
        parser.exit(2, 'Unable to create new evaluation directory.\n')
    print('Synthetic evaluation case created.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
