"""Тесты tools/llm.py: локальный HTTP-сервер, коды выхода, журнал, секреты."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fixtures  # noqa: F401
import journal

TOOLS = fixtures.TOOLS
UNAVAILABLE = "Вспомогательная модель недоступна — эту задачу выполняет головная модель"


class ScriptedServer:
    """HTTP-сервер с заранее заданными ответами; пишет полученные запросы."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.hits = 0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self):  # noqa: N802 - имя из http.server
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = {}
                server.requests.append(
                    {"path": self.path, "headers": dict(self.headers), "payload": payload}
                )
                index = server.hits
                server.hits += 1
                if server.responses:
                    item = server.responses[min(index, len(server.responses) - 1)]
                else:
                    item = {"status": 200, "content": "ok", "usage": {}}
                if item.get("delay"):
                    time.sleep(float(item["delay"]))
                status = int(item.get("status", 200))
                if status >= 400:
                    body = json.dumps({"error": item.get("content", "error")}).encode("utf-8")
                else:
                    body = json.dumps(
                        {
                            "choices": [{"message": {"content": item.get("content", "")}}],
                            "usage": item.get("usage", {}),
                        }
                    ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # noqa: D401 - тишина в тестах
                return

        return Handler


class LlmTests(unittest.TestCase):
    """Вызовы модели через локальный сервер."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "config").mkdir(parents=True)
        self.project = self.root / "project"
        (self.project / "state").mkdir(parents=True)
        (self.project / "state" / "journal.jsonl").write_text("", encoding="utf-8")
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("Выдели факты из текста.", encoding="utf-8")
        self.env = {
            "STRATEG_AGENT_ROOT": str(self.root),
            "STRATEG_PROJECTS": str(self.root / "projects"),
            "STRATEG_LLM_RETRY_DELAY": "0.01",
            "STRATEG_LLM_TIMEOUT": "5",
            "DEEPSEEK_API_KEY": "",
        }

    def _server(self, responses):
        server = ScriptedServer(responses)
        url = server.start()
        self.addCleanup(server.stop)
        return server, url

    def _write_routing(self, base_url: str = "http://127.0.0.1:9",
                       model: str = "test-model") -> None:
        routing = {
            "providers": {
                "deepseek": {"base_url": base_url, "key_env": "DEEPSEEK_API_KEY"}
            },
            "tasks": {
                "extract_facts": {
                    "executor": "helper", "provider": "deepseek",
                    "model": model, "fallback": "head",
                },
                "classify_request": {"executor": "head"},
            },
        }
        fixtures.write(self.root / "config" / "routing.json",
                       json.dumps(routing, ensure_ascii=False, indent=2))

    def _write_prices(self, models=None) -> None:
        fixtures.write(self.root / "config" / "prices.json",
                       json.dumps({"_note": "тест", "models": models or {}}, ensure_ascii=False))

    def _call(self, *extra, key: str = "test-key", task: str = "extract_facts",
              env_extra=None):
        env = dict(self.env)
        env["DEEPSEEK_API_KEY"] = key
        if env_extra:
            env.update(env_extra)
        return fixtures.run_script(
            TOOLS / "llm.py", "call", str(self.project),
            "--task", task, "--prompt-file", str(self.prompt), *extra, env=env,
        )

    def _events(self):
        return journal.read_events(self.project)

    def test_success(self) -> None:
        server, url = self._server(
            [{"status": 200, "content": "готово", "usage": {"prompt_tokens": 100,
                                                            "completion_tokens": 20}}]
        )
        self._write_routing(url)
        self._write_prices()
        result = self._call()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("готово", result.stdout)
        events = self._events()
        self.assertEqual([event["status"] for event in events], ["start", "done"])
        done = events[-1]
        self.assertEqual(done["tool"], "llm.py")
        self.assertEqual(done["tokens_in"], 100)
        self.assertEqual(done["tokens_out"], 20)
        self.assertEqual(done["cost_usd"], 0.0)
        self.assertIn("цена не задана", done["note"])
        self.assertEqual(server.hits, 1)
        self.assertEqual(server.requests[0]["payload"]["model"], "test-model")
        self.assertFalse(server.requests[0]["payload"]["stream"])
        self.assertTrue(server.requests[0]["headers"].get("Authorization", "").startswith("Bearer "))

    def test_retry_after_500(self) -> None:
        server, url = self._server([
            {"status": 500},
            {"status": 200, "content": "ок", "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        ])
        self._write_routing(url)
        self._write_prices()
        result = self._call()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(server.hits, 2)
        self.assertEqual([event["status"] for event in self._events()], ["start", "done"])

    def test_persistent_500_is_unavailable(self) -> None:
        server, url = self._server([{"status": 500}])
        self._write_routing(url)
        self._write_prices()
        result = self._call()
        self.assertEqual(result.returncode, 3)
        self.assertIn(UNAVAILABLE, result.stderr)
        self.assertEqual(server.hits, 3)
        self.assertEqual(self._events()[-1]["status"], "fallback")

    def test_no_key(self) -> None:
        self._write_routing()
        self._write_prices()
        result = self._call(key="")
        self.assertEqual(result.returncode, 3)
        self.assertIn(UNAVAILABLE, result.stderr)
        self.assertIn("нет ключа", result.stderr)
        self.assertEqual(self._events()[-1]["status"], "fallback")

    def test_model_placeholder(self) -> None:
        self._write_routing(model="ЗАПОЛНИТЬ")
        self._write_prices()
        result = self._call()
        self.assertEqual(result.returncode, 3)
        self.assertIn("модель не задана", result.stderr)
        self.assertEqual(self._events()[-1]["status"], "fallback")

    def test_invalid_json_twice(self) -> None:
        server, url = self._server([
            {"status": 200, "content": "это не json"},
            {"status": 200, "content": "и это не json"},
        ])
        self._write_routing(url)
        self._write_prices()
        result = self._call("--json")
        self.assertEqual(result.returncode, 4)
        self.assertIn(UNAVAILABLE, result.stderr)
        self.assertEqual(server.hits, 2)
        events = self._events()
        self.assertEqual(events[-1]["status"], "error")
        self.assertIn("невалидный JSON", events[-1]["note"])

    def test_invalid_json_then_valid_retry(self) -> None:
        server, url = self._server([
            {"status": 200, "content": "мусор", "usage": {"prompt_tokens": 10,
                                                          "completion_tokens": 1}},
            {"status": 200, "content": "{\"ok\": true}", "usage": {"prompt_tokens": 20,
                                                                   "completion_tokens": 2}},
        ])
        self._write_routing(url)
        self._write_prices()
        result = self._call("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"ok": True})
        self.assertEqual(server.hits, 2)
        retry_messages = server.requests[1]["payload"]["messages"]
        self.assertIn("Верни только валидный JSON", retry_messages[-1]["content"])
        done = self._events()[-1]
        self.assertEqual(done["tokens_in"], 30)
        self.assertEqual(done["tokens_out"], 3)

    def test_tokens_and_cost_in_journal(self) -> None:
        server, url = self._server(
            [{"status": 200, "content": "ok", "usage": {"prompt_tokens": 1000,
                                                        "completion_tokens": 500}}]
        )
        self._write_routing(url)
        self._write_prices({"test-model": {"input": 1.0, "output": 2.0}})
        result = self._call()
        self.assertEqual(result.returncode, 0, result.stderr)
        done = self._events()[-1]
        self.assertEqual(done["tokens_in"], 1000)
        self.assertEqual(done["tokens_out"], 500)
        self.assertAlmostEqual(done["cost_usd"], 0.002, places=6)
        self.assertNotIn("цена не задана", done["note"])

    def test_fenced_json_accepted(self) -> None:
        server, url = self._server(
            [{"status": 200, "content": "```json\n{\"answer\": 42}\n```", "usage": {}}]
        )
        self._write_routing(url)
        self._write_prices()
        result = self._call("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"answer": 42})

    def test_key_from_env_file(self) -> None:
        secret = "sk-from-env-999"
        fixtures.write(self.root / ".env", f"DEEPSEEK_API_KEY={secret}\n")
        server, url = self._server([{"status": 200, "content": "ok", "usage": {}}])
        self._write_routing(url)
        self._write_prices()
        result = self._call(key="")
        self.assertEqual(result.returncode, 0, result.stderr)
        journal_text = (self.project / "state" / "journal.jsonl").read_text(encoding="utf-8")
        for text in (result.stdout, result.stderr, journal_text):
            self.assertNotIn(secret, text)

    def test_key_never_leaks(self) -> None:
        secret = "sk-test-secret-12345"
        server, url = self._server([{"status": 200, "content": "ok", "usage": {}}])
        self._write_routing(url)
        self._write_prices()
        result = self._call(key=secret)
        self.assertEqual(result.returncode, 0, result.stderr)
        journal_text = (self.project / "state" / "journal.jsonl").read_text(encoding="utf-8")
        for text in (result.stdout, result.stderr, journal_text):
            self.assertNotIn(secret, text)

    def test_system_and_max_tokens_forwarded(self) -> None:
        system = self.root / "system.txt"
        system.write_text("Ты аналитик.", encoding="utf-8")
        server, url = self._server([{"status": 200, "content": "ok", "usage": {}}])
        self._write_routing(url)
        self._write_prices()
        result = self._call("--system-file", str(system), "--max-tokens", "42")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = server.requests[0]["payload"]
        self.assertEqual(payload["max_tokens"], 42)
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "Ты аналитик."})
        self.assertIn("model", payload)

    def test_task_for_head_is_unavailable(self) -> None:
        self._write_routing()
        self._write_prices()
        result = self._call(task="classify_request")
        self.assertEqual(result.returncode, 3)
        self.assertIn("головной моделью", result.stderr)

    def test_unknown_task_is_unavailable(self) -> None:
        self._write_routing()
        self._write_prices()
        result = self._call(task="no_such_task")
        self.assertEqual(result.returncode, 3)
        self.assertIn("не описана", result.stderr)


if __name__ == "__main__":
    unittest.main()
