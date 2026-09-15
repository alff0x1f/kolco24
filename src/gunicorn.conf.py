import os

# Picked up automatically: gunicorn reads ./gunicorn.conf.py from its cwd (/app).
# threads > 1 switches the worker class to gthread, so a request waiting on the
# bank does not block the whole worker.
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
# Above the VTB client's worst case (token 15 s + create_order 20 s), so a slow
# bank answer is not cut off halfway through creating the order.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
