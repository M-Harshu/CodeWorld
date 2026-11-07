from app.services.order_service import OrderService


class FakeOrders:
    def save(self, order):
        return order


def test_place_order():
    svc = OrderService(FakeOrders())
    assert svc.place_order(1, [{"price": 2, "qty": 3}]).total == 6
