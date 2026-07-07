from pymongo import MongoClient
from pymongo.database import Database

from app.config import settings


def get_mongo() -> Database:
    client: MongoClient = MongoClient(settings.MONGO_URI)
    return client.get_default_database()
