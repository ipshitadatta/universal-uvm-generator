"""
LLM Client — Priority: Claude API → Gemini API → opencode CLI (free fallback)
Prompt trimming, retry logic, error signature caching.
Lessons from Siemens CAT-46518: trim prompts to 4000 tokens max, 3 retries,
cache error signatures to prevent re-diagnosing same bug repeatedly.
"""

import os
import json
import time
import subprocess
import urllib.request
import urllib.error
from typing import Optional

MAX_TOKENS_PER_CALL = 4000   # CAT-46518 lesson: full-file prompts → timeout
MAX_RETRIES         = 3
RETRY_DELAY_BASE    = 2      # seconds, exponential backoff


class LLMClient:
    def __init__(self):
        self.anthropic_key = os.environ.get('ANTHROPIC_API_KEY', '')
        self.gemini_key    = os.environ.get('GEMINI_API_KEY', '')

        # Determine backend
        if self.anthropic_key:
            self.backend = 'claude'
        elif self.gemini_key:
            self.backend = 'gemini'
        else:
            # Check opencode availability
            try:
                result = subprocess.run(
                    ['opencode', '--version'],
                    capture_output=True, timeout=5
                )
                self.backend = 'opencode'
            except (FileNotFoundError, subprocess.TimeoutExpired):
                print("[WARN] No LLM API keys found and opencode not available.")
                print("       Set ANTHROPIC_API_KEY or GEMINI_API_KEY in environment.")
                print("       Or install opencode: curl -fsSL https://opencode.ai/install | bash")
                self.backend = 'template_only'

        # Error signature cache: (error_code, file_type) → fix_patch
        self._error_cache = {}

    def call(self, prompt: str, system: str = '', max_tokens: int = 4096) -> str:
        """
        Call LLM with prompt. Returns response text.
        Trims prompt to MAX_TOKENS_PER_CALL before sending.
        """
        # Trim prompt if needed (CAT-46518 lesson: long prompts → timeout)
        prompt = self._trim_prompt(prompt, MAX_TOKENS_PER_CALL)

        for attempt in range(MAX_RETRIES):
            try:
                if self.backend == 'claude':
                    return self._call_claude(prompt, system, max_tokens)
                elif self.backend == 'gemini':
                    return self._call_gemini(prompt, system, max_tokens)
                elif self.backend == 'opencode':
                    return self._call_opencode(prompt)
                else:
                    return ''
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAY_BASE ** (attempt + 1)
                    print(f"    [LLM] Attempt {attempt+1} failed: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f"    [LLM] All {MAX_RETRIES} attempts failed: {e}")
                    return ''

        return ''

    def call_json(self, prompt: str, system: str = '', max_tokens: int = 4096) -> dict:
        """Call LLM and parse JSON response."""
        json_system = system + "\nRespond ONLY with valid JSON. No markdown, no preamble."
        response = self.call(prompt, system=json_system, max_tokens=max_tokens)
        if not response:
            return {}
        # Strip markdown fences if present
        response = response.strip()
        if response.startswith('```'):
            lines = response.split('\n')
            response = '\n'.join(lines[1:-1])
        response = response.strip()
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # Try to fix truncated JSON by closing open braces
            fixed = response
            open_b = fixed.count('{') - fixed.count('}')
            open_s = fixed.count('[') - fixed.count(']')
            # Remove trailing partial line
            lines = fixed.split('\n')
            while lines and not lines[-1].strip().endswith(('}',']','"',',')):
                lines.pop()
            fixed = '\n'.join(lines)
            fixed += ']' * open_s + '}' * open_b
            try:
                return json.loads(fixed)
            except json.JSONDecodeError as e:
                print(f"    [LLM] JSON parse failed: {e}")
                return {}

    def fix_error(self, error_code: str, file_type: str, context: str) -> Optional[dict]:
        """
        LLM-based error fixer with signature caching.
        Returns: {'old_str': ..., 'new_str': ...} or None
        CAT-46518 lesson: cache by (error_code, file_type) to avoid
        re-diagnosing the same bug 30+ times across tests.
        """
        cache_key = f"{error_code}::{file_type}"
        if cache_key in self._error_cache:
            print(f"    [FIX] Using cached fix for {cache_key}")
            return self._error_cache[cache_key]

        prompt = f"""You are a SystemVerilog/UVM expert.

Fix this QuestaSim compile error:
Error code: {error_code}
File type: {file_type}
Context:
{context}

Respond with JSON:
{{
  "explanation": "what caused this error",
  "old_str": "exact text to replace (must match exactly)",
  "new_str": "replacement text"
}}"""

        result = self.call_json(prompt)
        if result and 'old_str' in result and 'new_str' in result:
            self._error_cache[cache_key] = result
            return result
        return None

    # ─── Backend implementations ──────────────────────────────

    def _call_claude(self, prompt: str, system: str, max_tokens: int) -> str:
        """Call Anthropic Claude API."""
        import urllib.request
        url = 'https://api.anthropic.com/v1/messages'
        payload = json.dumps({
            'model': 'claude-sonnet-4-6',
            'max_tokens': max_tokens,
            'system': system if system else 'You are an expert SystemVerilog and UVM verification engineer.',
            'messages': [{'role': 'user', 'content': prompt}]
        }).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': self.anthropic_key,
                'anthropic-version': '2023-06-01'
            }
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            return data['content'][0]['text']

    def _call_gemini(self, prompt: str, system: str, max_tokens: int) -> str:
        """Call Google Gemini API."""
        model = 'gemini-3.6-flash'
        url = (f'https://generativelanguage.googleapis.com/v1beta/models/'
               f'{model}:generateContent?key={self.gemini_key}')
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        payload = json.dumps({
            'contents': [{'parts': [{'text': full_prompt}]}],
            'generationConfig': {'maxOutputTokens': max_tokens}
        }).encode()

        req = urllib.request.Request(
            url, data=payload,
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            return data['candidates'][0]['content']['parts'][0]['text']

    def _call_opencode(self, prompt: str) -> str:
        """Call opencode CLI (free, no API key needed)."""
        # Write prompt to temp file to avoid shell quoting issues (CAT-46518 lesson)
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(prompt)
            tmp = f.name

        try:
            result = subprocess.run(
                ['opencode', 'run', f'$(cat {tmp})',
                 '--model', 'opencode/deepseek-v4-flash-free'],
                capture_output=True, text=True, timeout=60
            )
            return result.stdout.strip()
        finally:
            os.unlink(tmp)

    def _trim_prompt(self, prompt: str, max_tokens: int) -> str:
        """
        Trim prompt to approximately max_tokens.
        CAT-46518 lesson: 1 token ≈ 4 chars for English/code.
        Keep first 30% and last 70% when trimming — preserve context + the key request.
        """
        max_chars = max_tokens * 4
        if len(prompt) <= max_chars:
            return prompt

        # Keep first 30% (context/preamble) and last 70% (actual request)
        keep_start = int(max_chars * 0.3)
        keep_end   = int(max_chars * 0.7)
        trimmed = (
            prompt[:keep_start] +
            f'\n\n[... {len(prompt) - keep_start - keep_end} chars trimmed for LLM token limit ...]\n\n' +
            prompt[-keep_end:]
        )
        return trimmed
