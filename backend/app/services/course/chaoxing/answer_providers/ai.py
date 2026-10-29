import json
import math
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

import httpx
from loguru import logger

from ..answer_base import Tiku
from ..api_usage import record_response
from .. import query_control as control
from ..answer_utils import (
    _clean_option_prefix,
    _ensure_answer_list,
    _prepare_option_lines,
    _strip_json_block,
)
from ..endpoint_security import assert_public_endpoint, is_public_endpoint

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_MIN_INTERVAL_SECONDS = 3.0
_DEFAULT_RETRY_DELAY = 2.0


def _parse_attempt_count(value, default: int = _DEFAULT_MAX_RETRIES) -> int:
    """Parse total attempts while keeping one request for a zero setting."""
    try:
        if isinstance(value, bool):
            parsed = int(value)
        elif isinstance(value, int):
            parsed = value
        elif isinstance(value, float):
            if not math.isfinite(value) or not value.is_integer():
                raise ValueError
            parsed = int(value)
        elif isinstance(value, str):
            parsed = int(value.strip(), 10)
        else:
            parsed = int(value)
        if parsed < 0:
            raise ValueError
        return max(1, parsed)
    except (TypeError, ValueError, OverflowError):
        return default


def _parse_non_negative_finite(value, default: float) -> float:
    """Parse a delay-like setting, falling back for invalid/non-finite values."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed) or parsed < 0:
        return default
    return parsed


def _safe_status_code(value: object) -> int | None:
    """Return an HTTP status suitable for diagnostics, without user data."""
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    return None


class _AnswerResponseError(Exception):
    """A received answer is unusable; another paid generation is not a retry."""


class AI(Tiku):
    """AI大模型答题实现，带重试与更鲁棒的解析"""

    def __init__(self) -> None:
        super().__init__()
        self.name = "AI大模型答题"
        self.last_request_time = None
        # NOTE: this provider talks to the model over httpx (self._httpx_client),
        # not the `openai` SDK; `OpenAI` was never imported. Drop the dead type
        # reference (the attribute is always None / unused here). (ruff F821)
        self.client = None
        self._httpx_client: httpx.Client | None = None
        self.http_proxy: str | None = None
        self.min_interval_seconds: float = _DEFAULT_MIN_INTERVAL_SECONDS
        self.timeout: float = 30
        self.max_retries: int = _DEFAULT_MAX_RETRIES
        self.retry_delay: float = _DEFAULT_RETRY_DELAY
        self.disable_ssl_verify: bool = False
        self.max_output_tokens = 512
        self._interval_lock = threading.Lock()
        # 全局并发控制：限制同一 AI 客户端同时在请求中的题目数量
        self.max_active_requests: int = 3
        self._request_semaphore: threading.Semaphore | None = None
        # 精简提示词：直接输出 JSON，禁止多余内容
        self._system_prompts = {
            "single": (
                "单选题答题。直接输出JSON，禁止解释、思考过程或Markdown。\n"
                '格式：{"Answer": ["B"]}  （B为正确选项字母，仅填A/B/C/D之一）'
            ),
            "multiple": (
                "多选题答题。直接输出JSON，禁止解释、思考过程或Markdown。\n"
                '格式：{"Answer": ["A", "C"]}  （填所有正确选项字母）'
            ),
            "completion": (
                "填空题答题。直接输出JSON，禁止解释、思考过程或Markdown。\n"
                '格式：{"Answer": ["答案1", "答案2"]}  （按空格顺序填写答案）'
            ),
            "judgement": (
                "判断题答题。直接输出JSON，禁止解释、思考过程或Markdown。\n"
                '格式：{"Answer": ["正确"]} 或 {"Answer": ["错误"]}'
            ),
            "shortanswer": (
                "简答题答题。直接回答题目要求，给出具体要点，禁止解释作答过程或Markdown。\n"
                "通用方法类问题按一般原则作答，不要求提供具体事件。"
                "若题干无法识别或确实无法作答，返回空数组，不把无法确定等说明当作答案。\n"
                '格式：{"Answer": ["完整答案"]}；无法作答：{"Answer": []}'
            ),
            "default": ("答题助手。直接输出JSON，禁止解释、思考过程或Markdown。\n" '格式：{"Answer": ["答案"]}'),
        }

    def _build_client(self):
        httpx_kwargs: dict[str, Any] = {"timeout": self.timeout}
        httpx_kwargs["follow_redirects"] = False
        if self.http_proxy:
            httpx_kwargs["proxy"] = self.http_proxy
        if self.disable_ssl_verify:
            httpx_kwargs["verify"] = False
        if self._httpx_client:
            try:
                self._httpx_client.close()
            except Exception:
                logger.debug("AI题库旧 HTTP 客户端关闭失败")
        self._httpx_client = httpx.Client(**httpx_kwargs)

    def _respect_interval(self):
        min_interval_seconds = _parse_non_negative_finite(
            self.min_interval_seconds,
            _DEFAULT_MIN_INTERVAL_SECONDS,
        )
        sleep_time = 0
        with self._interval_lock:
            if self.last_request_time:
                interval_time = time.time() - self.last_request_time
                if interval_time < min_interval_seconds:
                    sleep_time = min_interval_seconds - interval_time
            self.last_request_time = time.time()
        if sleep_time > 0:
            logger.debug(f"AI请求间隔过短, 等待 {sleep_time:.2f} 秒")
            control.sleep(sleep_time)

    def _build_messages(self, q_info: dict) -> list[dict]:
        options = [_clean_option_prefix(opt) for opt in _prepare_option_lines(q_info.get("options", []))]
        q_type = q_info.get("type", "single")
        system_prompt = self._system_prompts.get(q_type, self._system_prompts["default"])
        user_content = f"题目：{q_info.get('title', '')}".strip()
        if options:
            user_content = f"{user_content}\n选项：{chr(10).join(options)}"
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]

    def _invoke_completion(self, messages: list[dict]) -> str | None:
        # Keep the selected key local to each request: concurrent questions
        # must never mutate a shared self.key while another request is retrying.
        keys = getattr(self, "_keys", None) or [self.key]
        for index, key in enumerate(keys):
            control.check_control()
            try:
                answer = self._invoke_completion_with_key(messages, key, "primary" if index == 0 else "fallback")
            except _AnswerResponseError:
                logger.warning("AI响应无法使用，停止该题重试及备用 Key 调用")
                return None
            if answer is not None:
                return answer
            if index + 1 < len(keys):
                logger.warning("AI优先 Key 调用失败，切换到回退 Key")
        return None

    def _invoke_completion_with_key(self, messages: list[dict], key: str, role: str = "primary") -> str | None:
        if not self._httpx_client:
            logger.error("AI题库 HTTP 客户端未初始化")
            return None
        last_error_type: str | None = None
        max_attempts = _parse_attempt_count(self.max_retries)
        if control.active_query():
            max_attempts = min(max_attempts, 2 if len(getattr(self, "_keys", ()) or ()) > 1 else 3)
        retry_delay = _parse_non_negative_finite(self.retry_delay, _DEFAULT_RETRY_DELAY)
        min_interval_seconds = _parse_non_negative_finite(
            self.min_interval_seconds,
            _DEFAULT_MIN_INTERVAL_SECONDS,
        )
        for attempt in range(1, max_attempts + 1):
            sem = self._request_semaphore
            acquired = False
            attempt_status_code: int | None = None
            try:
                if not is_public_endpoint(getattr(self, "endpoint", "")) or (
                    self.http_proxy and not is_public_endpoint(self.http_proxy)
                ):
                    logger.error("AI题库请求地址校验失败")
                    return None
                if sem is not None:
                    if control.active_query():
                        while not acquired:
                            acquired = sem.acquire(timeout=min(0.2, control.remaining()))
                    else:
                        sem.acquire()
                        acquired = True
                control.check_control()
                self._respect_interval()
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": self.max_output_tokens,
                }
                if urlsplit(self.endpoint).hostname == "api.deepseek.com":
                    payload["thinking"] = {"type": "disabled"}
                request_started = time.monotonic()
                control.claim_attempt("AI", 3)
                try:
                    response = self._httpx_client.post(
                        self.endpoint,
                        headers=headers,
                        json=payload,
                        follow_redirects=False,
                        **({"timeout": control.request_timeout(self.timeout)} if control.active_query() else {}),
                    )
                except Exception:
                    record_response("AI", role, attempt, request_started)
                    raise
                record_response("AI", role, attempt, request_started, response)
                control.check_control()
                if acquired:
                    sem.release()
                    acquired = False
                attempt_status_code = _safe_status_code(response.status_code)
                if attempt_status_code != 200:
                    safe_status = attempt_status_code if attempt_status_code is not None else "unknown"
                    if attempt_status_code in {401, 402, 403}:
                        # Invalid credentials / insufficient balance cannot be
                        # repaired by retrying the same key. Never log bodies.
                        logger.warning(f"AI Key 不可用，HTTP {safe_status}")
                        return None
                    raise RuntimeError(f"HTTP status {safe_status}")

                data = response.json()
                if data["choices"][0].get("finish_reason") == "length":
                    raise _AnswerResponseError()
                raw_content = data["choices"][0]["message"]["content"] or ""

                # 去掉 <think> 和 </think> 标记本身，但保留其中的内容，以便从中提取 Answer JSON
                text_without_tags = re.sub(r"(?is)</?think>", "", raw_content)
                base_text = (text_without_tags or "").strip()
                if not base_text:
                    logger.warning("AI大模型返回内容为空（仅包含 <think> 标签或空白），将视为无答案处理")
                    raise _AnswerResponseError()

                answers: list[str] = []

                # 优先从文本中提取包含 Answer/answer 字段的 JSON 段，取最后一个作为最终答案
                json_candidate = None
                try:
                    pattern = r'\{[^{}]*(?:"Answer"|"answer")\s*:\s*\[[^\]]*\][^{}]*\}'
                    matches = re.findall(pattern, base_text, flags=re.DOTALL)
                    if matches:
                        json_candidate = matches[-1].strip()
                    else:
                        # 回退到去除 Markdown 代码块后的整体文本
                        json_candidate = _strip_json_block(base_text)
                except Exception:
                    json_candidate = _strip_json_block(base_text)

                json_candidate_stripped = (json_candidate or "").strip()

                if json_candidate_stripped:
                    # 优先按JSON解析；若失败，再尝试将单引号风格的字典转换为JSON
                    try:
                        payload = json.loads(json_candidate_stripped)
                        answers = _ensure_answer_list((payload.get("Answer") or payload.get("answer")) if isinstance(payload, dict) else payload if isinstance(payload, list) else None)
                    except json.JSONDecodeError:
                        payload = None
                        candidate_fixed = json_candidate_stripped
                        # 针对形如 {'Answer': ['输入/输出']} 的内容，尝试将单引号替换为双引号后再次解析
                        if "'" in candidate_fixed and '"' not in candidate_fixed:
                            try:
                                candidate_fixed = candidate_fixed.replace("'", '"')
                                payload = json.loads(candidate_fixed)
                            except Exception:
                                payload = None
                        if payload is not None:
                            answers = _ensure_answer_list((payload.get("Answer") or payload.get("answer")) if isinstance(payload, dict) else payload if isinstance(payload, list) else None)
                        else:
                            logger.warning("AI大模型返回内容不是标准JSON，将按纯文本处理")
                            answers = _ensure_answer_list(base_text)
                # 没有可用的 JSON 片段，直接按纯文本处理
                elif base_text:
                    answers = _ensure_answer_list(base_text)
                else:
                    logger.warning("AI大模型返回内容为空，将视为无答案处理")
                    return None

                if not answers:
                    logger.warning("AI大模型返回空答案，将视为无答案处理")
                    raise _AnswerResponseError()
                return "\n".join(answers).strip()
            except (control.QueryControlError, control.ProviderUnavailable):
                raise
            except Exception as exc:
                if attempt_status_code == 200:
                    raise _AnswerResponseError() from None
                # Only transport/server/rate-limit failures warrant retries.
                if attempt_status_code is not None and attempt_status_code != 429 and attempt_status_code < 500:
                    raise _AnswerResponseError() from None
                last_error_type = type(exc).__name__
                if attempt_status_code == 429:
                    cool_down = max(min_interval_seconds * 2, 5)
                    logger.warning(
                        f"AI大模型请求失败 ({attempt}/{max_attempts}) 且触发限流 [{last_error_type}]，"
                        f"HTTP {attempt_status_code}，将休眠 {cool_down:.2f} 秒"
                    )
                    if attempt < max_attempts:
                        control.sleep(cool_down)
                else:
                    status_suffix = f"，HTTP {attempt_status_code}" if attempt_status_code is not None else ""
                    logger.warning(f"AI大模型请求失败 ({attempt}/{max_attempts}) [{last_error_type}]{status_suffix}")
                    if attempt < max_attempts:
                        control.sleep(retry_delay * attempt)
            finally:
                if acquired and sem is not None:
                    sem.release()
        if last_error_type:
            logger.error(f"AI大模型连续失败 [{last_error_type}]")
        else:
            logger.error("AI大模型连续失败")
        return None

    def _query(self, q_info: dict):
        messages = self._build_messages(q_info)
        return self._invoke_completion(messages)

    def _init_tiku(self):
        if os.environ.get('PROFILE') == 'local':
            # A settings save rotates endpoint and key files as one operation.
            # Do not initialize a task with a new key and the previous endpoint.
            from app.desktop_settings import LOCK
            with LOCK:
                return self._init_tiku_unlocked()
        return self._init_tiku_unlocked()

    def _init_tiku_unlocked(self):
        server_endpoint = os.environ.get("CHAOXING_AI_ENDPOINT", "").strip()
        endpoint = str(self._conf.get("endpoint") or server_endpoint).strip()
        model = str(self._conf.get("model") or os.environ.get("CHAOXING_AI_MODEL", "")).strip()
        key = str(self._conf.get("key") or "").strip()
        keys = []
        if not endpoint or not model:
            raise ValueError("AI endpoint and model must be configured")
        if not key:
            # Never attach the server credential to a caller-selected endpoint.
            if not server_endpoint or endpoint != server_endpoint:
                raise ValueError("Server AI key requires the configured server endpoint")
            # Existing deployment key remains the fallback. An optional primary
            # file can be populated independently, without exposing either key
            # in task settings or the browser.
            for variable in ("CHAOXING_AI_PRIMARY_KEY_FILE", "CHAOXING_AI_KEY_FILE"):
                key_file = os.environ.get(variable, "").strip()
                if key_file:
                    try:
                        candidate = Path(key_file).read_text(encoding="utf-8").strip()
                    except OSError:
                        raise ValueError("AI API key file cannot be read") from None
                    if candidate and candidate not in keys:
                        keys.append(candidate)
            if not keys:
                raise ValueError("AI API key has not been configured")
            key = keys[0]
        else:
            # Explicit caller credentials never inherit server fallback keys.
            keys = [key]
        self.endpoint = assert_public_endpoint(endpoint)
        # Keep the server key out of _conf: task payloads may be persisted.
        self.key = key
        self._keys = tuple(keys)
        self.model = model
        raw_proxy = self._conf.get("http_proxy")
        self.http_proxy = assert_public_endpoint(raw_proxy) if raw_proxy else None
        self.min_interval_seconds = _parse_non_negative_finite(
            self._conf.get("min_interval_seconds", _DEFAULT_MIN_INTERVAL_SECONDS),
            _DEFAULT_MIN_INTERVAL_SECONDS,
        )
        self.max_output_tokens = min(4096, _parse_attempt_count(self._conf.get("max_output_tokens", 512), 512))
        self.timeout = float(self._conf.get("timeout", os.environ.get("CHAOXING_AI_TIMEOUT_SECONDS", 30)))
        self.max_retries = _parse_attempt_count(self._conf.get("max_retries", _DEFAULT_MAX_RETRIES))
        self.retry_delay = _parse_non_negative_finite(
            self._conf.get("retry_delay", _DEFAULT_RETRY_DELAY),
            _DEFAULT_RETRY_DELAY,
        )
        self.disable_ssl_verify = str(self._conf.get("disable_ssl_verify", "false")).lower() in {
            "1",
            "true",
            "yes",
            "on",
            "y",
        }
        # 允许通过 ai_concurrency 配置最大同时在请求中的题目数量；缺省为 3
        max_req_raw = self._conf.get("ai_concurrency")
        try:
            self.max_active_requests = int(max_req_raw) if max_req_raw is not None else 3
        except (TypeError, ValueError):
            self.max_active_requests = 3
        self.max_active_requests = max(self.max_active_requests, 1)
        self._request_semaphore = threading.Semaphore(self.max_active_requests)
        self._build_client()
