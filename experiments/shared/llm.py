"""Single entry point for every LLM call (experiments/README.md rule 1).

- Anthropic via the official SDK (messages.with_raw_response.create, so rate-limit headers are kept).
- DeepSeek via the OpenAI SDK (base_url https://api.deepseek.com).
- Every call: reserve projected cost in the shared ledger -> call -> settle actual cost -> append a
  JSONL record (model, effort, tokens incl. cache, stop_reason, latency, cost, payload sha256).
- No temperature/top_p/top_k. No server-side fallbacks. stop_reason == "refusal" is a logged outcome.
- Keys come from env or experiments/.env and are never printed or logged.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from .cost_ledger import BudgetExceeded, CostLedger

EXPERIMENTS_DIR = Path(__file__).resolve().parent.parent

# USD per 1M tokens. Anthropic figures from the claude-api skill (models.md / prompt-caching.md):
# cache write (5m TTL) = 1.25x input; cache read = 0.1x input (Opus 5.5: 0.05x).
PRICES = {
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cw": 0.125, "cr": 0.01,
                         "long_threshold": 100_000, "long_in": 0.50, "long_out": 2.50},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cw": 2.50, "cr": 0.20},
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cw": 5.00, "cr": 0.20},
    # DeepSeek (USD/1M, PEAK rates = conservative; off-peak is half). Source: https://api-docs.deepseek.com/quick_start/pricing
    # fetched 2026-11-08 ~06:10 IST: deepseek-flash cache-miss input 0.30, cache-hit input 0.006, output 1.20 (peak).
    "deepseek-flash": {"in": 0.30, "out": 1.20, "cw": 0.30, "cr": 0.006},
    "deepseek-v4-pro": {"in": 1.32, "out": 3.96, "cw": 1.32, "cr": 0.044},
}

CHARS_PER_TOKEN_HEURISTIC = 2.8
DEEPSEEK_MODEL = "deepseek-flash"  # current id per DeepSeek docs (2026-11-08); served model is logged
DEEPSEEK_THINKING = "disabled"  # non-thinking mode, fixed for every DeepSeek call in the study (lead decision Nov 8 ~07:05 IST;
                                # dev pilots before that used "enabled": long thinking caused max_tokens/missing-key invalids)  # conservative (Claude 5.5 tokenizer counts ~30% more than older ones)


def load_env(path: Path | None = None) -> None:
    """Tiny .env loader. Does not override variables already set. Never prints values."""
    path = path or (EXPERIMENTS_DIR / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def have_key(name: str = "ANTHROPIC_API_KEY") -> bool:
    load_env()
    return bool(os.environ.get(name))


def _jsonable(x):
    if hasattr(x, "to_dict"):
        return x.to_dict()
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def canonical_sha256(obj) -> str:
    s = json.dumps(_jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def visible_payload(system: str, messages: list, tools: list | None, output_format: dict | None) -> dict:
    """Everything the model can see, excluding transport knobs (model id, max_tokens, cache markers)."""
    return {"system": system, "messages": _jsonable(messages), "tools": tools, "output_format": output_format}


def cost_usd(model: str, usage: dict) -> float:
    p = PRICES[model]
    inp = usage.get("input_tokens") or 0
    cw = usage.get("cache_creation_input_tokens") or 0
    cr = usage.get("cache_read_input_tokens") or 0
    out = usage.get("output_tokens") or 0
    pin, pout, pcw, pcr = p["in"], p["out"], p["cw"], p["cr"]
    if "long_threshold" in p and (inp + cw + cr) > p["long_threshold"]:
        f = p["long_in"] / p["in"]
        pin, pout, pcw, pcr = p["long_in"], p["long_out"], pcw * f, pcr * f
    return (inp * pin + cw * pcw + cr * pcr + out * pout) / 1e6


def heuristic_tokens(obj) -> int:
    s = json.dumps(_jsonable(obj), ensure_ascii=False, default=str)
    return int(len(s) / CHARS_PER_TOKEN_HEURISTIC) + 1


class LLM:
    """backend: 'anthropic' (live) or 'fake' (offline harness exercise; never reported as results)."""

    def __init__(self, run_dir, phase: str, sub: str | None = None, ledger: CostLedger | None = None,
                 backend: str = "anthropic", fake_responder=None, use_count_tokens: bool = True):
        self.run_dir = run_dir
        self.phase, self.sub = phase, sub
        self.ledger = ledger or CostLedger()
        self.backend = backend
        self.fake_responder = fake_responder
        self.use_count_tokens = use_count_tokens
        self._client = None
        self._ds_client = None

    # -- clients ----------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            import anthropic
            load_env()
            self._client = anthropic.Anthropic(max_retries=6, timeout=300.0)  # SDK backs off on 429/529/5xx
        return self._client

    @property
    def ds_client(self):
        if self._ds_client is None:
            from openai import OpenAI
            load_env()
            self._ds_client = OpenAI(api_key=os.environ.get("DEEPSEEK_API_KEY"),
                                     base_url="https://api.deepseek.com", max_retries=6, timeout=300.0)
        return self._ds_client

    # -- anthropic ----------------------------------------------------------
    def estimate_input_tokens(self, model, sys_blocks, messages, tools) -> tuple[int, str]:
        if self.backend == "anthropic" and self.use_count_tokens:
            try:
                kw = dict(model=model, system=sys_blocks, messages=messages)
                if tools:
                    kw["tools"] = tools
                return int(self.client.messages.count_tokens(**kw).input_tokens), "count_tokens"
            except Exception as e:  # fall back, but record why
                return heuristic_tokens([sys_blocks, messages, tools]), f"heuristic({type(e).__name__})"
        return heuristic_tokens([sys_blocks, messages, tools]), "heuristic"

    def call(self, *, model: str, system: str, messages: list, tools: list | None = None,
             output_format: dict | None = None, effort: str = "medium", max_tokens: int = 6000,
             cache_system: bool = True, top_level_cache: bool = False, tag: str = "",
             meta: dict | None = None) -> dict:
        call_id = "call_" + secrets.token_hex(6)
        sys_block = {"type": "text", "text": system}
        if cache_system:
            sys_block["cache_control"] = {"type": "ephemeral"}
        output_config = {"effort": effort}
        if output_format is not None:
            output_config["format"] = {"type": "json_schema", "schema": output_format}
        kwargs = dict(model=model, max_tokens=max_tokens, system=[sys_block], messages=messages,
                      thinking={"type": "adaptive"}, output_config=output_config)
        if tools:
            kwargs["tools"] = tools
        if top_level_cache:
            kwargs["cache_control"] = {"type": "ephemeral"}

        vis_sha = canonical_sha256(visible_payload(system, messages, tools, output_format))
        req_sha = canonical_sha256(kwargs)
        est_in, est_method = self.estimate_input_tokens(model, [sys_block], messages, tools)
        p = PRICES[model]
        projected = (est_in * max(p["in"], p["cw"]) + max_tokens * p["out"]) / 1e6
        rec = {"call_id": call_id, "tag": tag, "provider": "anthropic", "backend": self.backend,
               "model": model, "effort": effort, "thinking": "adaptive", "max_tokens": max_tokens,
               "est_input_tokens": est_in, "est_method": est_method, "projected_usd": round(projected, 6),
               "visible_payload_sha256": vis_sha, "request_sha256": req_sha, "phase": self.phase,
               "sub": self.sub, "meta": meta or {}, "cache_system": cache_system,
               "top_level_cache": top_level_cache}
        result = {"call_id": call_id, "text": "", "tool_uses": [], "content": [], "content_api": [],
                  "outcome": None, "record": rec}

        if self.backend == "fake":
            res_id = None
        else:
            try:
                res_id = self.ledger.reserve(self.phase, projected, sub=self.sub,
                                             meta={"call_id": call_id, "tag": tag, "model": model})
            except BudgetExceeded as e:
                rec.update(outcome="budget_refused", error=str(e))
                self.run_dir.log("llm_calls", rec)
                result["outcome"] = "budget_refused"
                return result

        t0 = time.perf_counter()
        try:
            if self.backend == "fake":
                msg = self.fake_responder(kwargs)
                headers = {}
                content_api = msg["content"]
                content = msg["content"]
                usage = msg["usage"]
                stop_reason, stop_details, served, request_id = msg["stop_reason"], None, model, None
            else:
                raw = self.client.messages.with_raw_response.create(**kwargs)
                headers = {k: v for k, v in raw.headers.items()
                           if k.lower().startswith("anthropic-ratelimit") or k.lower() in ("retry-after", "request-id")}
                m = raw.parse()
                content_api = m.content
                content = [b.to_dict() for b in m.content]
                usage = m.usage.to_dict()
                stop_reason = m.stop_reason
                sd = getattr(m, "stop_details", None)
                stop_details = sd.to_dict() if hasattr(sd, "to_dict") else sd
                served, request_id = m.model, getattr(m, "_request_id", None)
        except Exception as e:  # API/status/connection/timeout errors are logged outcomes
            latency = time.perf_counter() - t0
            status = getattr(e, "status_code", None)
            hdrs = {}
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    hdrs = {k: v for k, v in resp.headers.items()
                            if k.lower().startswith("anthropic-ratelimit") or k.lower() in ("retry-after", "request-id")}
                except Exception:
                    pass
            if res_id:
                self.ledger.release(res_id, reason=f"error {type(e).__name__}")
            name = type(e).__name__
            outcome = "timeout" if "Timeout" in name else ("rate_limited" if status == 429 else "api_error")
            rec.update(outcome=outcome, error_type=name, status_code=status, error=str(e)[:500],
                       latency_s=round(latency, 3), ratelimit_headers=hdrs, cost_usd=0.0)
            self.run_dir.log("llm_calls", rec)
            result["outcome"] = outcome
            return result
        latency = time.perf_counter() - t0
        cost = cost_usd(model, usage)
        if res_id:
            self.ledger.settle(res_id, cost, meta={"call_id": call_id})
        outcome = {"refusal": "refusal", "max_tokens": "max_tokens"}.get(stop_reason, "ok")
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        tool_uses = [{"id": b["id"], "name": b["name"], "input": b.get("input", {})}
                     for b in content if b.get("type") == "tool_use"]
        rec.update(outcome=outcome, served_model=served, model_mismatch=(served != model),
                   stop_reason=stop_reason, stop_details=stop_details,
                   input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                   cache_creation_input_tokens=usage.get("cache_creation_input_tokens"),
                   cache_read_input_tokens=usage.get("cache_read_input_tokens"),
                   usage=usage, latency_s=round(latency, 3), cost_usd=round(cost, 8),
                   ratelimit_headers=headers, request_id=request_id, n_tool_uses=len(tool_uses),
                   block_types=[b.get("type") for b in content])
        self.run_dir.log("llm_calls", rec)
        self.run_dir.log("raw_calls", {"call_id": call_id, "request": _jsonable(kwargs), "response_content": content})
        result.update(text=text, tool_uses=tool_uses, content=content, content_api=content_api, outcome=outcome)
        return result

    # -- deepseek (OpenAI-compatible) ---------------------------------------
    def deepseek_chat(self, *, messages: list, tools: list | None = None, json_mode: bool = False,
                      model: str = DEEPSEEK_MODEL, max_tokens: int = 8000, tag: str = "", meta: dict | None = None) -> dict:
        """One DeepSeek chat-completions call. Thinking mode fixed by DEEPSEEK_THINKING (currently disabled); for tool
        conversations the caller must pass back `assistant_message` verbatim (it carries reasoning_content when thinking is on)."""
        call_id = "call_" + secrets.token_hex(6)
        kwargs = dict(model=model, messages=messages, max_tokens=max_tokens, extra_body={"thinking": {"type": DEEPSEEK_THINKING}})
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
        est_in = heuristic_tokens(messages)
        p = PRICES[model]
        projected = (est_in * p["in"] + max_tokens * p["out"]) / 1e6
        rec = {"call_id": call_id, "tag": tag, "provider": "deepseek", "backend": "deepseek", "model": model, "max_tokens": max_tokens,
               "thinking": DEEPSEEK_THINKING, "est_input_tokens": est_in, "est_method": "heuristic",
               "projected_usd": round(projected, 6),
               "visible_payload_sha256": canonical_sha256({"messages": messages, "tools": tools, "json_mode": json_mode}),
               "request_sha256": canonical_sha256(kwargs), "phase": "deepseek", "meta": meta or {}}
        out = {"call_id": call_id, "outcome": None, "text": "", "tool_calls": [], "assistant_message": None, "record": rec}
        if self.backend == "fake":  # offline exercise (Nov 8 fix): never reaches the network or the ledger
            n_tool = sum(1 for m in messages if m.get("role") == "tool")
            hook = getattr(self, "fake_ds_text", None)  # optional fake-only hook (Study 2 fake runs)
            tcs, text = [], (hook(messages) if hook else json.dumps({"rationale": "fake", "verdict": "insufficient_evidence", "suspicion": 5}))
            if tools and n_tool < 2:
                fn = tools[n_tool % len(tools)]["function"]
                args = {k: ("2026-12-31T00:00:00Z" if k == "as_of" else "x") for k in fn.get("parameters", {}).get("required", [])}
                tcs, text = [{"id": f"call_f{n_tool}", "name": fn["name"], "arguments": json.dumps(args)}], ""
            am = {"role": "assistant", "content": text}
            if tcs:
                am["tool_calls"] = [{"id": t["id"], "type": "function", "function": {"name": t["name"], "arguments": t["arguments"]}} for t in tcs]
            rec.update(backend="fake", outcome="ok", cost_usd=0.0, latency_s=0.0, n_tool_uses=len(tcs))
            self.run_dir.log("llm_calls", rec)
            out.update(outcome="ok", text=text, tool_calls=tcs, assistant_message=am)
            return out
        try:
            res_id = self.ledger.reserve("deepseek", projected, meta={"call_id": call_id, "tag": tag})
        except BudgetExceeded as e:
            rec.update(outcome="budget_refused", error=str(e))
            self.run_dir.log("llm_calls", rec)
            out["outcome"] = "budget_refused"
            return out
        t0 = time.perf_counter()
        try:
            r = self.ds_client.chat.completions.create(**kwargs)
        except Exception as e:
            self.ledger.release(res_id, reason=type(e).__name__)
            status = getattr(e, "status_code", None)
            oc = "timeout" if "Timeout" in type(e).__name__ else ("rate_limited" if status == 429 else "api_error")
            rec.update(outcome=oc, error_type=type(e).__name__, status_code=status, error=str(e)[:500],
                       latency_s=round(time.perf_counter() - t0, 3), cost_usd=0.0)
            self.run_dir.log("llm_calls", rec)
            out["outcome"] = oc
            return out
        latency = time.perf_counter() - t0
        u = r.usage.model_dump() if r.usage else {}
        hit = u.get("prompt_cache_hit_tokens") or 0
        miss = u.get("prompt_cache_miss_tokens")
        miss = (u.get("prompt_tokens", 0) - hit) if miss is None else miss
        usage = {"input_tokens": miss, "cache_read_input_tokens": hit, "cache_creation_input_tokens": 0,
                 "output_tokens": u.get("completion_tokens", 0)}
        cost = cost_usd(model, usage)
        self.ledger.settle(res_id, cost, meta={"call_id": call_id})
        choice = r.choices[0]
        msg = choice.message
        text = msg.content or ""
        reasoning = getattr(msg, "reasoning_content", None) or (msg.model_extra or {}).get("reasoning_content")
        tcs = [{"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments} for tc in (msg.tool_calls or [])]
        am = {"role": "assistant", "content": text}
        if reasoning is not None:
            am["reasoning_content"] = reasoning
        if tcs:
            am["tool_calls"] = [{"id": t["id"], "type": "function", "function": {"name": t["name"], "arguments": t["arguments"]}} for t in tcs]
        oc = "ok" if choice.finish_reason in ("stop", "tool_calls") else str(choice.finish_reason)
        rec.update(outcome=oc, served_model=r.model, model_mismatch=(r.model != model), stop_reason=choice.finish_reason, usage=u,
                   input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"], cache_read_input_tokens=hit,
                   cache_creation_input_tokens=0, latency_s=round(latency, 3), cost_usd=round(cost, 8), n_tool_uses=len(tcs))
        self.run_dir.log("llm_calls", rec)
        self.run_dir.log("raw_calls", {"call_id": call_id, "request": _jsonable(kwargs), "response_text": text,
                                       "reasoning_len": len(reasoning or ""), "tool_calls": tcs})
        out.update(outcome=oc, text=text, tool_calls=tcs, assistant_message=am)
        return out

    # -- openai (Study 4; added Nov 8 ~20:20 IST; additive: the Anthropic and DeepSeek paths are unchanged) ----------------
    @property
    def oa_client(self):
        if getattr(self, "_oa_client", None) is None:
            from openai import OpenAI
            load_env()
            self._oa_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), max_retries=2, timeout=300.0)  # default base_url
        return self._oa_client

    def openai_chat(self, *, model: str, messages: list, tools: list | None = None, json_mode: bool = False, max_tokens: int = 16000,
                    reasoning_effort: str | None = "medium", tag: str = "", meta: dict | None = None) -> dict:
        """One OpenAI chat-completions call (mirrors deepseek_chat's contract). Token-ledgered (shared/token_ledger.py,
        provider openai, phase study4_openai): the per-run ceiling STUDY4_MAX_TOKENS is checked BEFORE the call.
        `reasoning_effort` is sent when the model accepts it; if the API rejects the parameter, the call is retried once
        without it and the fallback is logged (`reasoning_effort_used`). `max_tokens` maps to max_completion_tokens."""
        from shared.token_ledger import TokenBudgetExceeded, TokenLedger
        call_id = "call_" + secrets.token_hex(6)
        est_in = heuristic_tokens(messages)
        projected = est_in + max_tokens
        rec = {"call_id": call_id, "tag": tag, "provider": "openai", "backend": "openai", "model": model, "max_tokens": max_tokens,
               "reasoning_effort_requested": reasoning_effort, "est_input_tokens": est_in, "est_method": "heuristic",
               "projected_tokens": projected,
               "visible_payload_sha256": canonical_sha256({"messages": messages, "tools": tools, "json_mode": json_mode}),
               "phase": "study4_openai", "meta": meta or {}}
        out = {"call_id": call_id, "outcome": None, "text": "", "tool_calls": [], "assistant_message": None, "record": rec}
        if self.backend == "fake":  # offline exercise: no network, no ledger
            n_tool = sum(1 for m in messages if m.get("role") == "tool")
            hook = getattr(self, "fake_oa_text", None) or getattr(self, "fake_ds_text", None)
            tcs, text = [], (hook(messages) if hook else json.dumps({"rationale": "fake", "verdict": "insufficient_evidence", "suspicion": 5}))
            if tools and n_tool < 2:
                fn = tools[n_tool % len(tools)]["function"]
                args = {k: ("2026-12-31T00:00:00Z" if k == "as_of" else "x") for k in fn.get("parameters", {}).get("required", [])}
                tcs, text = [{"id": f"call_f{n_tool}", "name": fn["name"], "arguments": json.dumps(args)}], ""
            am = {"role": "assistant", "content": text}
            if tcs:
                am["tool_calls"] = [{"id": t["id"], "type": "function", "function": {"name": t["name"], "arguments": t["arguments"]}} for t in tcs]
            rec.update(backend="fake", outcome="ok", latency_s=0.0, n_tool_uses=len(tcs), reasoning_effort_used=reasoning_effort)
            self.run_dir.log("llm_calls", rec)
            out.update(outcome="ok", text=text, tool_calls=tcs, assistant_message=am)
            return out
        tl = TokenLedger()
        try:
            res_id = tl.reserve("study4_openai", self.run_dir.run_id, projected, meta={"call_id": call_id, "tag": tag, "model": model},
                                est_in=est_in)
        except TokenBudgetExceeded as e:
            rec.update(outcome="budget_refused", error=str(e))
            self.run_dir.log("llm_calls", rec)
            out["outcome"] = "budget_refused"
            return out
        kwargs = dict(model=model, messages=messages, max_completion_tokens=max_tokens)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
        effort_used = None
        t0 = time.perf_counter()
        try:
            try:
                r = self.oa_client.chat.completions.create(**kwargs, **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}))
                effort_used = reasoning_effort
            except Exception as e:  # parameter not supported by this model -> retry once without it (logged)
                msg = str(e).lower()
                if reasoning_effort and getattr(e, "status_code", None) == 400 and ("reasoning_effort" in msg or "reasoning" in msg):
                    rec["reasoning_effort_fallback"] = str(e)[:300]
                    r = self.oa_client.chat.completions.create(**kwargs)
                else:
                    raise
        except Exception as e:
            tl.release(res_id, reason=type(e).__name__)
            status = getattr(e, "status_code", None)
            oc = "timeout" if "Timeout" in type(e).__name__ else ("rate_limited" if status == 429 else "api_error")
            rec.update(outcome=oc, error_type=type(e).__name__, status_code=status, error=str(e)[:500], latency_s=round(time.perf_counter() - t0, 3))
            self.run_dir.log("llm_calls", rec)
            out["outcome"] = oc
            return out
        latency = time.perf_counter() - t0
        u = r.usage.model_dump() if r.usage else {}
        cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        reasoning = ((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        usage = {"input": u.get("prompt_tokens", 0) or 0, "cached": cached, "output": u.get("completion_tokens", 0) or 0, "reasoning": reasoning}
        tl.settle(res_id, usage, meta={"call_id": call_id})
        choice = r.choices[0]
        msg = choice.message
        text = msg.content or ""
        tcs = [{"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments} for tc in (msg.tool_calls or [])]
        am = {"role": "assistant", "content": text}
        if tcs:
            am["tool_calls"] = [{"id": t["id"], "type": "function", "function": {"name": t["name"], "arguments": t["arguments"]}} for t in tcs]
        refusal = getattr(msg, "refusal", None)
        oc = ("refusal" if refusal else ("ok" if choice.finish_reason in ("stop", "tool_calls") else str(choice.finish_reason)))
        rec.update(outcome=oc, served_model=r.model, model_mismatch=(not str(r.model).startswith(model)), stop_reason=choice.finish_reason,
                   usage=u, input_tokens=usage["input"], cached_input_tokens=cached, output_tokens=usage["output"], reasoning_tokens=reasoning,
                   latency_s=round(latency, 3), n_tool_uses=len(tcs), reasoning_effort_used=effort_used, cost_usd=0.0)
        self.run_dir.log("llm_calls", rec)
        self.run_dir.log("raw_calls", {"call_id": call_id, "request": _jsonable({k: v for k, v in kwargs.items()}), "response_text": text,
                                       "tool_calls": tcs, "refusal": refusal})
        out.update(outcome=oc, text=text, tool_calls=tcs, assistant_message=am)
        return out

    def deepseek_call(self, *, system: str, user: str, model: str = DEEPSEEK_MODEL, max_tokens: int = 8000,
                      json_mode: bool = True, tag: str = "", meta: dict | None = None) -> dict:
        return self.deepseek_chat(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                                  json_mode=json_mode, model=model, max_tokens=max_tokens, tag=tag, meta=meta)
