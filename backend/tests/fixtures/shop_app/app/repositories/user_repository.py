import sqlite3
from app.models.user import User


class UserRepository:
    """Stores and loads users from the database."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def find_by_email(self, email: str):
        cur = self.conn.cursor()
        cur.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (email,))
        row = cur.fetchone()
        return User(*row) if row else None

    def add(self, email: str, password_hash: str) -> User:
        cur = self.conn.cursor()
        cur.execute("INSERT INTO users(email, password_hash) VALUES (?, ?)", (email, password_hash))
        self.conn.commit()
        return User(cur.lastrowid, email, password_hash)

    def search(self, term):
        cur = self.conn.cursor()
        cur.execute(f"SELECT * FROM users WHERE email LIKE '%{term}%'")
        return cur.fetchall()
