"""
QA AI Studio
Production Ollama Provider

Version: 7.0
"""

import json
import time
import threading
import requests

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from Config import settings
from Core.ai_provider import AIProvider
from Core.logger import Logger


class OllamaProvider(AIProvider):

    _session = None
    _session_lock = threading.Lock()
    _health_cache = None
    _health_time = 0

    # --------------------------------------------------

    def __init__(self):

        self.logger = Logger.get_logger()

        self.base_url = settings.OLLAMA_URL.replace(
            "/api/generate",
            ""
        )

        self.model = settings.LLM_MODEL

        self.timeout = getattr(
            settings,
            "LLM_TIMEOUT",
            300
        )

        self.keep_alive = getattr(
            settings,
            "OLLAMA_KEEP_ALIVE",
            "30m"
        )

        self.temperature = getattr(
            settings,
            "LLM_TEMPERATURE",
            0.2
        )

        self.num_ctx = getattr(
            settings,
            "OLLAMA_NUM_CTX",
            4096
        )

        self.num_predict = getattr(
            settings,
            "DEFAULT_MAX_TOKENS",
            1024
        )

        self.top_k = getattr(
            settings,
            "OLLAMA_TOP_K",
            40
        )

        self.top_p = getattr(
            settings,
            "OLLAMA_TOP_P",
            0.9
        )

        self.repeat_penalty = getattr(
            settings,
            "OLLAMA_REPEAT_PENALTY",
            1.1
        )

        self.mirostat = getattr(
            settings,
            "OLLAMA_MIROSTAT",
            0
        )

        if OllamaProvider._session is None:

            with OllamaProvider._session_lock:

                if OllamaProvider._session is None:

                    OllamaProvider._session = self._create_session()

        self.session = OllamaProvider._session

        self.logger.info(

            "Ollama Provider v7.0 initialized | "
            f"Model={self.model} | "
            f"Timeout={self.timeout}s"

        )

    # --------------------------------------------------

    @property
    def provider_name(self):

        return "Ollama"
    # --------------------------------------------------
    # Constructor
    # --------------------------------------------------

    def __init__(self):

        self.logger = Logger.get_logger()

        self.base_url = settings.OLLAMA_URL.replace(
            "/api/generate",
            ""
        )

        self.model = settings.LLM_MODEL

        self.timeout = getattr(
            settings,
            "LLM_TIMEOUT",
            300
        )

        self.keep_alive = getattr(
            settings,
            "OLLAMA_KEEP_ALIVE",
            "30m"
        )

        self.temperature = getattr(
            settings,
            "LLM_TEMPERATURE",
            0.2
        )

        self.num_ctx = getattr(
            settings,
            "OLLAMA_NUM_CTX",
            4096
        )

        self.num_predict = getattr(
            settings,
            "DEFAULT_MAX_TOKENS",
            1024
        )

        self.top_k = getattr(
            settings,
            "OLLAMA_TOP_K",
            40
        )


        self.top_p = getattr(
            settings,
            "OLLAMA_TOP_P",
            0.9
        )


        self.repeat_penalty = getattr(
            settings,
            "OLLAMA_REPEAT_PENALTY",
            1.1
        )


        self.mirostat = getattr(
            settings,
            "OLLAMA_MIROSTAT",
            0
        )

        self.session = self._create_session()

        self.logger.info(
            f"Ollama Provider v7.0 initialized | "
            f"Model={self.model} | "
            f"Timeout={self.timeout}s"
        )

    # --------------------------------------------------

    @property
    def provider_name(self):

        return "Ollama"

    # --------------------------------------------------
    # HTTP Session
    # --------------------------------------------------

    def _create_session(self):

        session = requests.Session()

        retry = Retry(

            total=2,

            connect=2,

            read=2,

            backoff_factor=1,

            status_forcelist=[
                429,
                500,
                502,
                503,
                504
            ],

            allowed_methods=[
                "POST",
                "GET"
            ]

        )

        adapter = HTTPAdapter(

            max_retries=retry,

            pool_connections=50,

            pool_maxsize=50

        )

        session.mount(
            "http://",
            adapter
        )

        session.mount(
            "https://",
            adapter
        )

        session.headers.update({

            "Content-Type": "application/json"

        })

        return session

    # --------------------------------------------------
    # Availability Check
    # --------------------------------------------------

    def is_available(self):

        try:

            response = self.session.get(

                f"{self.base_url}/api/tags",

                timeout=3

            )

            return response.status_code == 200

        except Exception:

            return False
            # --------------------------------------------------
    # Payload Builder
    # --------------------------------------------------

    def _payload(

        self,

        prompt,

        system_prompt=None,

        temperature=None,

        max_tokens=None,

        stream=False

    ):

        if temperature is None:
            temperature = self.temperature

        if max_tokens is None:
            max_tokens = self.num_predict

        payload = {

            "model": self.model,

            "prompt": prompt,

            "stream": stream,

            "keep_alive": self.keep_alive,

            "options": {

                "temperature": temperature,

                "num_ctx": self.num_ctx,

                "num_predict": max_tokens,

                "top_k": self.top_k,

                "top_p": self.top_p,

                "repeat_penalty": self.repeat_penalty,

                "mirostat": self.mirostat

            }

        }

        if system_prompt:

            payload["system"] = system_prompt

        return payload

    # --------------------------------------------------
    # Generate
    # --------------------------------------------------

    def generate(

        self,

        prompt,

        system_prompt=None,

        temperature=None,

        max_tokens=None,

        stream=False

    ):

        start = time.perf_counter()

        payload = self._payload(

            prompt=prompt,

            system_prompt=system_prompt,

            temperature=temperature,

            max_tokens=max_tokens,

            stream=stream

        )

        try:

            response = self.session.post(

                f"{self.base_url}/api/generate",

                json=payload,

                timeout=(
                    10,
                    self.timeout
                ),

                stream=stream

            )

            response.raise_for_status()

            if stream:

                return self._stream_response(
                    response
                )

            data = response.json()

            elapsed = round(

                time.perf_counter() - start,

                2

            )

            eval_count = data.get(
                "eval_count",
                0
            )

            prompt_eval = data.get(
                "prompt_eval_count",
                0
            )

            total_duration = data.get(
                "total_duration",
                0
            )

            load_duration = data.get(
                "load_duration",
                0
            )

            eval_duration = data.get(
                "eval_duration",
                0
            )

            tokens_per_sec = 0

            if eval_duration:

                try:

                    tokens_per_sec = round(

                        eval_count /

                        (eval_duration / 1_000_000_000),

                        2

                    )

                except Exception:

                    tokens_per_sec = 0

            self.logger.info(

                "Ollama generation completed | "

                f"Time={elapsed}s | "

                f"PromptTokens={prompt_eval} | "

                f"OutputTokens={eval_count} | "

                f"Speed={tokens_per_sec} tok/s"

            )

            text = data.get(

                "response",

                ""

            )

            if text:

                text = text.strip()

            if not text:

                return {

                    "success": False,

                    "provider": self.provider_name,

                    "model": self.model,

                    "error": "Model returned empty response."

                }

            return {

                "success": True,

                "provider": self.provider_name,

                "model": self.model,

                "response": text,

                "prompt_eval_count": prompt_eval,

                "eval_count": eval_count,

                "tokens_per_second": tokens_per_sec,

                "total_duration": total_duration,

                "load_duration": load_duration,

                "eval_duration": eval_duration,

                "execution_time": elapsed

            }

        except requests.exceptions.Timeout:

            self.logger.error(

                "Ollama request timeout."

            )

            return {

                "success": False,

                "provider": self.provider_name,

                "model": self.model,

                "error": f"Timeout after {self.timeout} seconds."

            }

        except requests.exceptions.ConnectionError:

            self.logger.error(

                "Unable to connect to Ollama."

            )

            return {

                "success": False,

                "provider": self.provider_name,

                "model": self.model,

                "error": "Unable to connect to Ollama."

            }

        except Exception as ex:

            self.logger.exception(

                "Ollama generation failed."

            )

            return {

                "success": False,

                "provider": self.provider_name,

                "model": self.model,

                "error": str(ex)

            }
            # --------------------------------------------------
    # Streaming Generate
    # --------------------------------------------------

    def stream_generate(

        self,

        prompt,

        system_prompt=None,

        temperature=None,

        max_tokens=None

    ):

        payload = self._payload(

            prompt=prompt,

            system_prompt=system_prompt,

            temperature=temperature,

            max_tokens=max_tokens,

            stream=True

        )

        try:

            response = self.session.post(

                f"{self.base_url}/api/generate",

                json=payload,

                timeout=(

                    10,

                    self.timeout

                ),

                stream=True

            )

            response.raise_for_status()

            return self._stream_response(
                response
            )

        except Exception as ex:

            self.logger.exception(

                "Ollama streaming request failed."

            )

            raise RuntimeError(

                str(ex)

            ) from ex


    # --------------------------------------------------
    # Stream Response Parser
    # --------------------------------------------------

    def _stream_response(

        self,

        response

    ):

        try:

            for line in response.iter_lines(

                decode_unicode=True

            ):

                if not line:

                    continue


                try:

                    data = json.loads(
                        line
                    )

                except Exception:

                    continue


                chunk = data.get(

                    "response",

                    ""

                )


                if chunk:

                    yield chunk


                if data.get(

                    "done",

                    False

                ):

                    break


        except Exception as ex:

            self.logger.exception(

                "Ollama stream parsing failed."

            )

            raise RuntimeError(

                str(ex)

            ) from ex


        finally:

            try:

                response.close()

            except Exception:

                pass


    # --------------------------------------------------
    # Chat
    # --------------------------------------------------

    def chat(

        self,

        messages,

        system_prompt=None,

        temperature=None,

        max_tokens=None

    ):


        formatted = []


        for message in messages:


            role = message.get(

                "role",

                "user"

            )


            content = message.get(

                "content",

                ""

            )


            formatted.append(

                f"{role}: {content}"

            )


        prompt = "\n\n".join(

            formatted

        )


        return self.generate(

            prompt=prompt,

            system_prompt=system_prompt,

            temperature=temperature,

            max_tokens=max_tokens

        )


    # --------------------------------------------------
    # Health Check
    # --------------------------------------------------

    def health_check(self):

        return self.is_available()


    # --------------------------------------------------
    # Warmup
    # --------------------------------------------------

    def warmup(self):

        try:

            self.logger.info(

                "Ollama warmup started."

            )


            start = time.perf_counter()


            result = self.generate(

                prompt=".",

                temperature=0,

                max_tokens=1

            )


            elapsed = round(

                time.perf_counter() - start,

                2

            )


            if result.get(

                "success"

            ):


                self.logger.info(

                    f"Ollama warmup completed | Time={elapsed}s"

                )


            else:

                self.logger.warning(

                    "Ollama warmup failed."

                )


            return result


        except Exception as ex:


            self.logger.exception(

                "Warmup failed."

            )


            return {

                "success": False,

                "error": str(ex)

            }


    # --------------------------------------------------
    # Close Session
    # --------------------------------------------------

    def close(self):

        try:

            if self.session:

                self.session.close()


                self.logger.info(

                    "Ollama session closed."

                )


        except Exception:


            pass
            # --------------------------------------------------
    # Model Information
    # --------------------------------------------------

    def model_info(self):

        return {

            "provider": self.provider_name,

            "model": self.model,

            "base_url": self.base_url,

            "timeout": self.timeout,

            "keep_alive": self.keep_alive,

            "context_size": self.num_ctx,

            "max_tokens": self.num_predict

        }


    # --------------------------------------------------
    # Safe Generate Wrapper
    # --------------------------------------------------

    def safe_generate(

        self,

        prompt,

        system_prompt=None,

        temperature=None,

        max_tokens=None

    ):

        try:

            result = self.generate(

                prompt=prompt,

                system_prompt=system_prompt,

                temperature=temperature,

                max_tokens=max_tokens

            )

            return result


        except Exception as ex:


            self.logger.exception(

                "Safe generation failed."

            )


            return {

                "success": False,

                "provider": self.provider_name,

                "model": self.model,

                "error": str(ex)

            }


    # --------------------------------------------------
    # Destructor
    # --------------------------------------------------

    def __del__(self):

        try:

            self.close()

        except Exception:

            pass