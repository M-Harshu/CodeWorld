from app.repositories.order_repository import OrderRepository
from app.models.order import Order
from app.utils.legacy import total_price_a


class OrderService:
    def __init__(self, orders: OrderRepository):
        self.orders = orders

    def place_order(self, user_id: int, items: list) -> Order:
        """Create and persist an order for a user."""
        order = Order(0, user_id, items, total_price_a(items))
        return self.orders.save(order)

    def history(self, user_id: int):
        return self.orders.for_user(user_id)
