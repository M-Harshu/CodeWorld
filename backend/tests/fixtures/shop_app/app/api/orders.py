from fastapi import APIRouter
from app.services.order_service import OrderService
from app.repositories.order_repository import OrderRepository
from app.api.deps import get_connection

router = APIRouter()


@router.post("/orders")
def create_order(user_id: int, items: list):
    service = OrderService(OrderRepository(get_connection()))
    order = service.place_order(user_id, items)
    return {"order_id": order.id}


@router.get("/orders/{user_id}")
def list_orders(user_id: int):
    service = OrderService(OrderRepository(get_connection()))
    return service.history(user_id)
