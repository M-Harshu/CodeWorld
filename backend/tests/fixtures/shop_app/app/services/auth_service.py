from app.repositories.user_repository import UserRepository
from app.utils.security import hash_password, verify_password, make_token


class AuthService:
    """Handles registration and login."""

    def __init__(self, users: UserRepository):
        self.users = users

    def register(self, email: str, password: str):
        if self.users.find_by_email(email):
            raise ValueError("email already registered")
        return self.users.add(email, hash_password(password))

    def login(self, email: str, password: str):
        user = self.users.find_by_email(email)
        if not user or not verify_password(password, user.password_hash):
            return None
        return make_token()
