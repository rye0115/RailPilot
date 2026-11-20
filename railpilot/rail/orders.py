"""Convert official order rows to the minimum data needed for local reconciliation."""

import hashlib
import re

from ..core.journey import payment_deadline
from ..models import Order, OrderTicket
from ..security.redact import mask_order_no


def _day(text: object) -> str:
    value = str(text or "")
    if re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value[:10] if re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[ T])", value) else ""


def parse_order(row: dict, *, payment_list: bool = False) -> Order:
    raw_no = str(row.get("sequence_no") or row.get("order_no") or row.get("orderId") or "").strip()
    status_text = str(row.get("order_status_name") or row.get("status") or row.get("order_status") or "")
    tickets: list[OrderTicket] = []
    ticket_statuses: set[str] = set()
    for item in row.get("tickets") or []:
        if not isinstance(item, dict):
            raise ValueError("订单车票数据不完整")
        train = item.get("stationTrainDTO") or {}
        person = item.get("passengerDTO") or {}
        if not isinstance(train, dict) or not isinstance(person, dict):
            raise ValueError("订单行程或乘车人数据不完整")
        tickets.append(OrderTicket(
            _day(item.get("train_date_local") or item.get("start_train_date_page") or train.get("train_date") or row.get("train_date")),
            str(train.get("station_train_code") or row.get("train_code") or ""),
            str(train.get("from_station_name") or ""), str(train.get("to_station_name") or ""),
            str(item.get("seat_type_name") or ""), str(person.get("passenger_name") or ""),
        ))
        ticket_statuses.add(str(item.get("ticket_status_name") or ""))
    if not status_text and len(ticket_statuses) == 1:
        status_text = next(iter(ticket_statuses))
    statuses = {
        "待支付": "unpaid", "等待支付": "unpaid", "未支付": "unpaid", "unpaid": "unpaid",
        "已支付": "paid", "已完成": "paid", "paid": "paid", "已取消": "cancelled", "cancelled": "cancelled",
    }
    payable = payment_list and raw_no and tickets and status_text in ("", "已订票", "待出票", "未出票")
    return Order(
        masked_no=mask_order_no(raw_no),
        status=statuses.get(status_text, "unpaid" if payable else "unknown"),
        identity=hashlib.sha256(raw_no.encode()).hexdigest() if raw_no else "",
        tickets=tuple(tickets), train_no=tickets[0].train_no if tickets else row.get("train_code") or row.get("train_no"),
        date=tickets[0].date if tickets else row.get("train_date") or row.get("date"),
        amount=row.get("ticket_total_price_page") or row.get("ticket_price") or row.get("amount"),
        pay_deadline=payment_deadline(row.get("pay_limit_time") or row.get("pay_deadline")),
        placed_at=payment_deadline(row.get("order_date")),
    )
