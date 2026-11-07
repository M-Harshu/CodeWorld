from app.services.auth_service import AuthService
from app.utils.security import hash_password


class FakeRepo:
    def __init__(self):
        self.users = {}

    def find_by_email(self, email):
        return self.users.get(email)

    def add(self, email, password_hash):
        self.users[email] = type("U", (), {"id": 1, "password_hash": password_hash})()
        return self.users[email]


def test_register_and_login():
    svc = AuthService(FakeRepo())
    svc.register("a@b.c", "pw")
    assert svc.login("a@b.c", "pw")


def test_hash_is_stable():
    assert hash_password("x") == hash_password("x")
