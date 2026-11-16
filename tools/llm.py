#!/usr/bin/env python3
"""Вызов вспомогательной модели с запасным путём на головную.

Команда ``call`` берёт провайдера и модель из ``config/routing.json`` по имени
задачи, шлёт OpenAI-совместимый запрос и печатает ответ в stdout. Каждый вызов
пишется в ``state/journal.jsonl`` (start → done/error/fallback).

Коды выхода:
  0 — успех;
  3 — модель недоступна (нет ключа, модель не задана, сеть, таймаут, HTTP 5xx/429
      после двух повторов);
  4 — ответ не прошёл проверку (в том числе невалидный JSON в режиме --json).

При кодах 3 и 4 в stderr печатается: «Вспомогательная модель недоступна — эту
задачу выполняет головная модель» и причина.

Примеры:
  python3 tools/llm.py call projects/demo --task extract_facts \\
      --prompt-file drafts/prompt.txt --json --stage S2.1
  python3 tools/llm.py call projects/demo --task tag_claims \\
      --prompt-file drafts/prompt.txt --system-file drafts/system.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE = Path(__file__).resolve().parents[1]
TOOLS = BASE / "tools"
AGENT_ROOT = Path(os.environ.get("STRATEG_AGENT_ROOT", str(BASE)))

try:  # поддержка запуска и как скрипта, и как модуля пакета
    from . import journal
except ImportError:  # pragma: no cover - зависит от способа запуска
    sys.path.insert(0, str(TOOLS))
    import journal  # type: ignore

UNAVAILABLE_MESSAGE = "Вспомогательная модель недоступна — эту задачу выполняет головная модель"
PLACEHOLDER = "ЗАПОЛНИТЬ"
JSON_RETRY_PROMPT = "Верни только валидный JSON без пояснений и без Markdown."


class Unavailable(Exception):
    """Модель недоступна: код 3."""


class InvalidResponse(Exception):
    """Ответ не прошёл проверку: код 4."""


class RequestError(Exception):
    """Ошибка HTTP/сети с признаком, стоит ли повторять."""

    def __init__(self, reason: str, retryable: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def _load_json(path: Path) -> Dict[str, Any]:
    """Читает JSON-конфиг."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise Unavailable(f"не читается {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise Unavailable(f"{path}: ожидался объект JSON")
    return payload


def _read_env(path: Path) -> Dict[str, str]:
    """Читает KEY=VALUE из .env (без вывода значений)."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _get_key(provider_cfg: Dict[str, Any]) -> Optional[str]:
    """Ищет ключ в окружении, затем в .env в корне агента."""
    name = str(provider_cfg.get("key_env") or "").strip()
    if not name:
        return None
    value = os.environ.get(name)
    if value:
        return value.strip()
    value = _read_env(AGENT_ROOT / ".env").get(name)
    return value or None


def _build_messages(prompt: str, system: Optional[str]) -> List[Dict[str, str]]:
    """Собирает messages для chat/completions."""
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _request(url: str, payload: Dict[str, Any], api_key: str,
             timeout: float) -> Dict[str, Any]:
    """Один POST-запрос к OpenAI-совместимому эндпоинту."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        retryable = status == 429 or 500 <= status < 600
        raise RequestError(f"HTTP {status}", retryable) from exc
    except socket.timeout as exc:
        raise RequestError("таймаут", True) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.timeout):
            raise RequestError("таймаут", True) from exc
        raise RequestError(f"сеть: {reason}", True) from exc
    except TimeoutError as exc:
        raise RequestError("таймаут", True) from exc
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise InvalidResponse("ответ сервера не JSON") from exc
    if not isinstance(parsed, dict):
        raise InvalidResponse("ответ сервера не объект JSON")
    return parsed


def _request_with_retries(url: str, payload: Dict[str, Any], api_key: str,
                          timeout: float, delay: float) -> Dict[str, Any]:
    """Повторяет 5xx/429 и сетевые ошибки дважды."""
    retries_left = 2
    while True:
        try:
            return _request(url, payload, api_key, timeout)
        except RequestError as exc:
            if exc.retryable and retries_left > 0:
                retries_left -= 1
                time.sleep(delay)
                continue
            suffix = " после 2 повторов" if exc.retryable else ""
            raise Unavailable(f"{exc.reason}{suffix}") from exc


def _content_and_usage(response: Dict[str, Any], stats: Dict[str, Any]) -> str:
    """Достаёт текст ответа и накапливает usage."""
    usage = response.get("usage") or {}
    if isinstance(usage, dict):
        stats["in"] += int(usage.get("prompt_tokens") or 0)
        stats["out"] += int(usage.get("completion_tokens") or 0)
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise InvalidResponse("в ответе нет choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise InvalidResponse("в ответе нет текста")
    return content


def _extract_json(text: str) -> Any:
    """Пытается достать JSON из ответа, в том числе из Markdown-блока."""
    candidates: List[str] = [text.strip()]
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    candidates.extend(item.strip() for item in fenced)
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _cost(stats: Dict[str, Any]) -> Tuple[float, str]:
    """Считает стоимость по config/prices.json; возвращает (cost, note)."""
    price = stats.get("price")
    if not isinstance(price, dict):
        return 0.0, "цена не задана"
    try:
        cost = (stats["in"] / 1_000_000.0) * float(price.get("input", 0))
        cost += (stats["out"] / 1_000_000.0) * float(price.get("output", 0))
    except (TypeError, ValueError):
        return 0.0, "цена не задана"
    return round(cost, 8), ""


def _load_prices(model: Optional[str]) -> Optional[Dict[str, Any]]:
    """Возвращает цену модели или None."""
    if not model:
        return None
    path = AGENT_ROOT / "config" / "prices.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, dict):
        return None
    entry = models.get(model)
    return entry if isinstance(entry, dict) else None


def _resolve_task(task: str) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """По имени задачи возвращает (routing, provider_cfg, model)."""
    routing = _load_json(AGENT_ROOT / "config" / "routing.json")
    tasks = routing.get("tasks") or {}
    providers = routing.get("providers") or {}
    cfg = tasks.get(task)
    if not isinstance(cfg, dict):
        raise Unavailable(f"задача {task!r} не описана в config/routing.json")
    if cfg.get("executor") != "helper":
        raise Unavailable(f"задача {task!r} выполняется головной моделью")
    model = str(cfg.get("model") or "").strip()
    if not model or model == PLACEHOLDER:
        raise Unavailable("модель не задана в config/routing.json")
    provider_name = str(cfg.get("provider") or "").strip()
    provider_cfg = providers.get(provider_name)
    if not isinstance(provider_cfg, dict):
        raise Unavailable(f"провайдер {provider_name!r} не описан в config/routing.json")
    return routing, provider_cfg, model


def run_call(project_dir: Path, task: str, prompt: str, system: Optional[str],
             json_mode: bool, max_tokens: Optional[int], stage: str,
             stats: Dict[str, Any]) -> str:
    """Выполняет вызов модели; возвращает текст для stdout."""
    routing, provider_cfg, model = _resolve_task(task)
    stats["model"] = model
    stats["price"] = _load_prices(model)
    stats["routing"] = routing

    api_key = _get_key(provider_cfg)
    if not api_key:
        key_name = str(provider_cfg.get("key_env") or "ключ")
        raise Unavailable(f"нет ключа {key_name} (окружение и .env пусты)")
    stats["_key"] = api_key

    base_url = str(provider_cfg.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise Unavailable("не задан base_url провайдера")
    url = base_url + "/chat/completions"

    messages = _build_messages(prompt, system)
    payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)

    timeout_raw = os.environ.get("STRATEG_LLM_TIMEOUT", "60")
    delay_raw = os.environ.get("STRATEG_LLM_RETRY_DELAY", "1.5")
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 60.0
    try:
        delay = float(delay_raw)
    except ValueError:
        delay = 1.5

    response = _request_with_retries(url, payload, api_key, timeout, delay)
    content = _content_and_usage(response, stats)

    if not json_mode:
        return content

    parsed = _extract_json(content)
    if parsed is not None:
        return json.dumps(parsed, ensure_ascii=False)

    retry_messages = messages + [
        {"role": "assistant", "content": content},
        {"role": "user", "content": JSON_RETRY_PROMPT},
    ]
    retry_payload = dict(payload)
    retry_payload["messages"] = retry_messages
    second = _request_with_retries(url, retry_payload, api_key, timeout, delay)
    second_content = _content_and_usage(second, stats)
    parsed = _extract_json(second_content)
    if parsed is None:
        raise InvalidResponse("невалидный JSON после повтора")
    return json.dumps(parsed, ensure_ascii=False)


def _journal_common(project_dir: Path, stage: str, key: str,
                    stats: Dict[str, Any], note: str) -> None:
    """Пишет терминальное событие журнала."""
    cost, price_note = _cost(stats)
    if price_note and note:
        note = f"{note}; {price_note}"
    elif price_note:
        note = price_note
    journal.append_event(
        project_dir,
        "done",
        stage,
        key,
        tool="llm.py",
        model=stats.get("model"),
        tokens_in=int(stats["in"]),
        tokens_out=int(stats["out"]),
        cost_usd=cost,
        note=note,
    )


def build_parser() -> argparse.ArgumentParser:
    """Собирает парсер команд вызова модели."""
    parser = argparse.ArgumentParser(
        prog="llm.py",
        description="Вызов вспомогательной модели (OpenAI-совместимый API) с журналом.",
        epilog="Коды выхода: 0 — успех, 3 — модель недоступна, 4 — ответ не прошёл проверку.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    call = sub.add_parser("call", help="вызвать модель для задачи")
    call.add_argument("project_dir", help="папка проекта")
    call.add_argument("--task", required=True, help="имя задачи из config/routing.json")
    call.add_argument("--prompt-file", required=True, help="файл с промптом пользователя")
    call.add_argument("--system-file", default=None, help="файл с системным промптом")
    call.add_argument("--json", action="store_true", help="ожидать валидный JSON")
    call.add_argument("--max-tokens", type=int, default=None, help="лимит токенов ответа")
    call.add_argument("--stage", default=None, help="этап для журнала")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа CLI вызова модели."""
    args = build_parser().parse_args(argv)
    project_dir = Path(args.project_dir)
    if not project_dir.is_dir():
        print(f"ошибка: проект не найден: {project_dir}", file=sys.stderr)
        return 2
    stage = args.stage or f"llm:{args.task}"
    key = f"llm:{args.task}"

    try:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        system = Path(args.system_file).read_text(encoding="utf-8") if args.system_file else None
    except OSError as exc:
        print(f"ошибка: не читается файл промпта: {exc}", file=sys.stderr)
        return 2

    stats: Dict[str, Any] = {"in": 0, "out": 0, "model": None, "price": None}
    journal.append_event(
        project_dir, "start", stage, key, tool="llm.py",
        note=f"task={args.task}",
    )

    try:
        text = run_call(
            project_dir, args.task, prompt, system, args.json,
            args.max_tokens, stage, stats,
        )
    except Unavailable as exc:
        reason = _safe_reason(exc, stats)
        _write_terminal(project_dir, "fallback", stage, key, stats, reason)
        print(UNAVAILABLE_MESSAGE, file=sys.stderr)
        print(f"Причина: {reason}", file=sys.stderr)
        return 3
    except InvalidResponse as exc:
        reason = _safe_reason(exc, stats)
        _write_terminal(project_dir, "error", stage, key, stats, reason)
        print(UNAVAILABLE_MESSAGE, file=sys.stderr)
        print(f"Причина: {reason}", file=sys.stderr)
        return 4

    _journal_common(project_dir, stage, key, stats, "")
    if not text.endswith("\n"):
        text += "\n"
    sys.stdout.write(text)
    return 0


def _safe_reason(exc: Exception, stats: Dict[str, Any]) -> str:
    """Причина без ключей: ключ в журнал и stderr не попадает."""
    reason = str(exc)
    key_value = str(stats.pop("_key", "") or os.environ.get("DEEPSEEK_API_KEY") or "")
    if key_value:
        reason = reason.replace(key_value, "***")
    reason = re.sub(r"\b(?:sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{8,})\b", "***", reason)
    return reason


def _write_terminal(project_dir: Path, status: str, stage: str, key: str,
                    stats: Dict[str, Any], reason: str) -> None:
    """Пишет error/fallback в журнал."""
    cost, price_note = _cost(stats)
    note = reason
    if price_note:
        note = f"{reason}; {price_note}"
    journal.append_event(
        project_dir, status, stage, key, tool="llm.py",
        model=stats.get("model"),
        tokens_in=int(stats["in"]),
        tokens_out=int(stats["out"]),
        cost_usd=cost,
        note=note,
    )


if __name__ == "__main__":
    sys.exit(main())
