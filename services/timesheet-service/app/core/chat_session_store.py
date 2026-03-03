from __future__ import annotations

import json
import threading

import redis
from redis.exceptions import RedisError

from app.core.config import Settings


class ChatSessionStore:
    _memory_state: dict[str, dict] = {}
    _memory_lock = threading.Lock()

    def __init__(self, settings: Settings):
        self._ttl_seconds = settings.chat_session_ttl_seconds
        self._redis = None

        try:
            client = redis.Redis.from_url(
                settings.chat_session_redis_url,
                decode_responses=True,
            )
            client.ping()
            self._redis = client
        except RedisError:
            self._redis = None

    def get_state(self, session_id: str) -> dict:
        if self._redis is not None:
            try:
                value = self._redis.get(self._key(session_id))
                if not value:
                    return {}
                return json.loads(value)
            except (RedisError, json.JSONDecodeError):
                return {}

        with self._memory_lock:
            return dict(self._memory_state.get(session_id, {}))

    def save_state(self, session_id: str, state: dict) -> None:
        if self._redis is not None:
            try:
                self._redis.setex(
                    self._key(session_id),
                    self._ttl_seconds,
                    json.dumps(state),
                )
                return
            except RedisError:
                pass

        with self._memory_lock:
            self._memory_state[session_id] = dict(state)

    def clear_state(self, session_id: str) -> None:
        if self._redis is not None:
            try:
                self._redis.delete(self._key(session_id))
                return
            except RedisError:
                pass

        with self._memory_lock:
            self._memory_state.pop(session_id, None)

    @staticmethod
    def _key(session_id: str) -> str:
        return f"chat:timesheet:{session_id}"
