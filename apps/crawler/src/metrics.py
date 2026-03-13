from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

tasks_total = Counter(
    "crawler_tasks_total",
    "Total tasks processed",
    ["kind", "status"],
)

task_duration_seconds = Histogram(
    "crawler_task_duration_seconds",
    "Task duration in seconds",
    ["kind"],
    buckets=[1, 2, 5, 10, 15, 30, 60, 120, 300],
)

tasks_active = Gauge("crawler_tasks_active", "Currently running tasks")
tasks_queued = Gauge("crawler_tasks_queued", "Tasks waiting in domain queues")
db_pool_size = Gauge("crawler_db_pool_size", "Total connections in pool")
db_pool_idle = Gauge("crawler_db_pool_idle", "Idle connections in pool")


def start_metrics_server(port: int) -> None:
    start_http_server(port)
