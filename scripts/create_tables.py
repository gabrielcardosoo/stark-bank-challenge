"""Cria as tabelas no banco configurado em DATABASE_URL.

Roda uma vez antes de subir a aplicação. É idempotente: tabelas que já existem
são ignoradas.
"""
from app.adapters.db.connector import engine, init_database

if __name__ == "__main__":
    init_database()
    print(f"tabelas criadas em {engine.url.render_as_string(hide_password=True)}")
