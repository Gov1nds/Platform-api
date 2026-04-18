web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
worker: python -m arq app.workers.arq_worker.WorkerSettings
release: alembic upgrade head
