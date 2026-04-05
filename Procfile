web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
release: bash release.sh
worker: python -m app.scripts.run_scheduled_jobs --loop
