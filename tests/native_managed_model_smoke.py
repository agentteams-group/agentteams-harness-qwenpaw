"""Real SDK startup/configuration against a slow local catalog, no model calls."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request

from qwenpaw_worker.api import QwenPawApiClient as Client

class Catalog(BaseHTTPRequestHandler):
    calls = []
    def log_message(self, *args):
        pass
    def do_GET(self):
        type(self).calls.append(self.path)
        time.sleep(6)
        self.send_response(404)
        self.end_headers()

gateway = ThreadingHTTPServer(("127.0.0.1", 0), Catalog)
threading.Thread(target=gateway.serve_forever, daemon=True).start()
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
with tempfile.TemporaryDirectory() as directory:
    env = dict(os.environ, QWENPAW_WORKING_DIR=directory + "/work", QWENPAW_SECRET_DIR=directory + "/secret")
    with open(directory + "/native.log", "w+") as log:
        app = subprocess.Popen(["/opt/venv/qwenpaw/bin/qwenpaw", "app", "--host", "127.0.0.1", "--port", str(port)], env=env, stdout=log, stderr=log)
        try:
            endpoint = "http://127.0.0.1:" + str(port)
            deadline = time.monotonic() + 60
            while True:
                try:
                    with urllib.request.urlopen(endpoint + "/api/version", timeout=1):
                        break
                except OSError:
                    if time.monotonic() > deadline or app.poll() is not None:
                        raise RuntimeError("Native app did not become ready")
                    time.sleep(0.2)
            # Selecting a model also initializes the native agent on a cold
            # runner. Catalog.calls independently checks for remote discovery.
            client = Client(endpoint)
            result = client.configure_active_model("managed-fixture", "qwen3.6-plus", base_url="http://127.0.0.1:" + str(gateway.server_port) + "/v1", api_key="fixture-no-model-call", supports_image=True, supports_video=False)
            response_model = "managed-responses-fixture"
            client.configure_active_model("managed-responses", response_model,
                base_url="http://127.0.0.1:" + str(gateway.server_port) + "/v1",
                api_key="fixture-no-model-call", chat_model="OpenAIResponseModel")
            # A second managed refresh must retain the selected native protocol.
            client.configure_active_model("managed-responses", response_model)
            providers = client._request("GET", "/api/models")
            assert next(p for p in providers if p["id"] == "managed-responses")["chat_model"] == "OpenAIResponseModel"
            # Background discovery would otherwise start after returning config.
            time.sleep(0.5)
            assert not Catalog.calls, Catalog.calls
            assert result["active_llm"]["model"] == "qwen3.6-plus"
            print(json.dumps({"passed": True, "native_model_readback": True, "remote_catalog_calls": Catalog.calls, "model_calls": 0}))
        except Exception:
            # --rm and TemporaryDirectory otherwise discard the only native
            # evidence for a localhost management-API timeout in image CI.
            log.flush()
            log.seek(0)
            print(json.dumps({"remote_catalog_calls": Catalog.calls, "native_exit": app.poll()}), flush=True)
            print(log.read()[-8000:], flush=True)
            raise
        finally:
            app.terminate()
            try:
                app.wait(timeout=15)
            except subprocess.TimeoutExpired:
                app.kill()
                app.wait(timeout=5)
            gateway.shutdown()
