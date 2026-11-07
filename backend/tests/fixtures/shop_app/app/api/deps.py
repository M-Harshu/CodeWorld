import sqlite3
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService


def get_connection():
    return sqlite3.connect("shop.db")


def get_auth_service() -> AuthService:
    return AuthService(UserRepository(get_connection()))
