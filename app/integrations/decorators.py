"""Decorator wrappers for circuit breaker and rate limiter."""
from __future__ import annotations
import functools, logging
logger = logging.getLogger(__name__)

def circuit_breaker(name: str, failure_threshold: int = 5, recovery_timeout: int = 60):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                from app.integrations.circuit_breaker import connector_circuit_breaker, CircuitOpenError
                connector_circuit_breaker.before_request(connector_name=name, cooldown_seconds=recovery_timeout)
            except Exception as e:
                if "CircuitOpenError" in type(e).__name__:
                    raise
            try:
                result = await fn(*args, **kwargs)
                try:
                    from app.integrations.circuit_breaker import connector_circuit_breaker
                    connector_circuit_breaker.record_success(connector_name=name)
                except Exception:
                    pass
                return result
            except Exception as e:
                try:
                    from app.integrations.circuit_breaker import connector_circuit_breaker
                    connector_circuit_breaker.record_failure(connector_name=name,
                        threshold=failure_threshold/100 if failure_threshold < 1 else 0.5,
                        cooldown_seconds=recovery_timeout)
                except Exception:
                    pass
                raise
        return wrapper
    return decorator

def rate_limiter(name: str, max_calls: int = 60, period: int = 60):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                from app.integrations.rate_limiter import connector_rate_limiter
                decision = connector_rate_limiter.acquire(
                    connector_name=name,
                    max_requests_per_minute=max_calls if period <= 60 else max_calls * 60 // period)
                if not decision.allowed:
                    raise RuntimeError(f"Rate limit exceeded for {name}")
            except ImportError:
                pass
            return await fn(*args, **kwargs)
        return wrapper
    return decorator
