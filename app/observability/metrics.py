"""Custom Prometheus metrics (Blueprint §31.4, C32)."""
try:
    from prometheus_client import Counter, Histogram, Gauge

    REQUEST_LATENCY = Histogram(
        "pgi_http_request_seconds", "HTTP request latency",
        ["method", "route", "status"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0))
    EXT_API_CALLS = Counter(
        "pgi_ext_api_calls_total", "External API calls",
        ["provider", "endpoint", "status"])
    EXT_API_DURATION = Histogram(
        "pgi_ext_api_seconds", "External API call duration", ["provider"])
    FRESHNESS_STATUS = Gauge(
        "pgi_freshness_status_count", "Rows by freshness status", ["table", "status"])
    ARQ_JOB_DURATION = Histogram(
        "pgi_arq_job_seconds", "Background job duration", ["job_name"])
    ARQ_JOB_ERRORS = Counter(
        "pgi_arq_job_errors_total", "Background job errors", ["job_name"])
except ImportError:
    class _Noop:
        def labels(self, **kw): return self
        def observe(self, v): pass
        def inc(self, v=1): pass
        def set(self, v): pass
    REQUEST_LATENCY = _Noop()
    EXT_API_CALLS = _Noop()
    EXT_API_DURATION = _Noop()
    FRESHNESS_STATUS = _Noop()
    ARQ_JOB_DURATION = _Noop()
    ARQ_JOB_ERRORS = _Noop()
