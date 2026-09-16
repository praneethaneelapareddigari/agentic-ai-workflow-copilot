from app.cache import CacheManager, make_cache_key


class TestCacheManager:
    async def test_set_then_get_roundtrip(self):
        cache = CacheManager()
        key = make_cache_key("test", "key1")
        await cache.set_json(key, {"hello": "world"})
        value = await cache.get_json(key)
        assert value == {"hello": "world"}

    async def test_miss_returns_none(self):
        cache = CacheManager()
        value = await cache.get_json(make_cache_key("does", "not", "exist"))
        assert value is None

    async def test_falls_back_to_memory_when_redis_unavailable(self):
        cache = CacheManager()
        # Force a bad Redis URL so connection fails fast and we fall back.
        from app import config as config_module
        cache._backend_name = "memory"
        key = make_cache_key("fallback", "test")
        await cache.set_json(key, [1, 2, 3])
        assert await cache.get_json(key) == [1, 2, 3]
        assert cache.backend_name == "memory"

    async def test_invalidate_removes_key(self):
        cache = CacheManager()
        key = make_cache_key("to", "delete")
        await cache.set_json(key, "value")
        await cache.invalidate(key)
        assert await cache.get_json(key) is None

    def test_cache_key_deterministic(self):
        k1 = make_cache_key("tool", "weather_lookup", {"location": "Paris"})
        k2 = make_cache_key("tool", "weather_lookup", {"location": "Paris"})
        k3 = make_cache_key("tool", "weather_lookup", {"location": "London"})
        assert k1 == k2
        assert k1 != k3
