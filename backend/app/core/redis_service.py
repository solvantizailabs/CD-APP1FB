import redis
import os
import json
import time

class InMemoryRedis:
    def __init__(self):
        self.store = {}
        self.expirations = {}

    def get(self, name):
        now = time.time()
        if name in self.expirations and now > self.expirations[name]:
            del self.store[name]
            del self.expirations[name]
            return None
        return self.store.get(name)

    def set(self, name, value, ex=None):
        self.store[name] = value
        if ex:
            self.expirations[name] = time.time() + ex
        return True

    def setex(self, name, time, value):
        return self.set(name, value, ex=time)
    
    def delete(self, name):
        if name in self.store:
            del self.store[name]
        if name in self.expirations:
            del self.expirations[name]
        return 1

    def ttl(self, name):
        """Mirrors real Redis TTL semantics: -2 if the key doesn't exist,
        -1 if it exists with no expiration, else seconds remaining."""
        now = time.time()
        if name not in self.store:
            return -2
        if name not in self.expirations:
            return -1
        remaining = self.expirations[name] - now
        if remaining <= 0:
            del self.store[name]
            del self.expirations[name]
            return -2
        return int(remaining)

    def ping(self):
        return True

class RedisService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
            
        self.redis_host = os.getenv("REDIS_HOST", "localhost")
        self.redis_port = int(os.getenv("REDIS_PORT", 6379))
        self.redis_password = os.getenv("REDIS_PASSWORD", None)
        
        try:
            self.r = redis.Redis(
                host=self.redis_host, 
                port=self.redis_port, 
                password=self.redis_password, 
                decode_responses=True,
                socket_connect_timeout=2  # Fail fast
            )
            # Test connection
            self.r.ping()
            print(f"[Redis] Connected to Redis at {self.redis_host}:{self.redis_port}")
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
            print(f"[Redis] Connection failed: {e}")
            print("[Redis] Using In-Memory Fallback. Data will be lost on restart.")
            self.r = InMemoryRedis()
            
        self._initialized = True

    def save_session(self, session_id, session_data, ttl=3600):
        """Save session data to Redis with expiration"""
        try:
            self.r.set(session_id, json.dumps(session_data), ex=ttl)
        except Exception as e:
            print(f"Error saving session to Redis: {e}")

    def get_session(self, session_id):
        """Retrieve session data from Redis"""
        try:
            data = self.r.get(session_id)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            print(f"Error retrieving session from Redis: {e}")
            return None

redis_service = RedisService()
