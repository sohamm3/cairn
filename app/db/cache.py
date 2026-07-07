import redis

from app.config import settings


def get_redis() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL)
