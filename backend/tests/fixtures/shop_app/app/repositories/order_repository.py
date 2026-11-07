from app.models.order import Order
from app.api.auth import login   # layering violation on purpose: repository imports API


class OrderRepository:
    def __init__(self, conn):
        self.conn = conn

    def save(self, order: Order) -> Order:
        self.conn.execute("INSERT INTO orders(user_id, total) VALUES (?, ?)", (order.user_id, order.total))
        return order

    def for_user(self, user_id: int):
        return self.conn.execute("SELECT * FROM orders WHERE user_id = ?", (user_id,)).fetchall()
