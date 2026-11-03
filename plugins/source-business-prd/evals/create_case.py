#!/usr/bin/env python3
"""Create synthetic, intentionally incomplete source repositories for isolated evaluations."""

import argparse
from pathlib import Path


FILES = {
    'orders/service.py': '''AUTO_DISPATCH = False

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
    'request.md': '请分析 orders 与 warehouse 两个仓库，输出业务知识和现状 PRD，帮助产品经理理解用户目标、为什么做这些功能、全流程如何闭环。已有一份旧产品说明供参考，没有数据库连接和前端源码。请覆盖已有文件，可用流程或时序等 Markdown 图表表达。所有产物写入指定输出目录；不改源码、不调用外部系统。\n',
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
