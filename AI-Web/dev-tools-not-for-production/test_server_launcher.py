# TEST-ONLY — never part of the delivery, never imported by real code.
#
# This sandbox cannot reach huggingface.co (to download the real
# embedding/cross-encoder models) or a local Ollama server — both are
# network-restricted here, exactly as encountered and documented in
# the earlier Knowledge Hub / AI Assistant round of this same task.
# TestExecutionManager() unconditionally constructs AutomationGenerator()
# -> RAGEngine(), which eagerly loads a real SentenceTransformer /
# CrossEncoder at *construction* time — meaning even an operation that
# has nothing to do with AI (like importing a plain Excel file of test
# cases) fails here purely because the model download is blocked, not
# because of anything wrong with the QA Automation code being tested.
#
# This monkeypatches sentence_transformers.SentenceTransformer /
# CrossEncoder with deterministic, offline fakes, and
# Core.ollama_provider.OllamaProvider with a fake that returns
# syntactically-valid canned output, BEFORE importing Web.main — so
# the real QA Automation code paths (import, list, script save,
# validate, execute, git, environment settings, history) can be
# exercised against a REAL running server for genuine end-to-end
# verification, without needing real network access. AI-generated
# script/text QUALITY is explicitly NOT verified by any test that
# uses this launcher — only that the plumbing around it behaves
# correctly (a real request goes in, a real response comes back, the
# right thing gets persisted).
import sys
import types
import numpy as np


class _FakeSentenceTransformer:
    def __init__(self, *args, **kwargs):
        pass

    def encode(self, text, convert_to_numpy=True, **kwargs):
        if isinstance(text, str):
            texts = [text]
            single = True
        else:
            texts = list(text)
            single = False

        vectors = []
        for t in texts:
            seed = abs(hash(t)) % (2**32)
            rng = np.random.RandomState(seed)
            vectors.append(rng.rand(384).astype("float32"))

        arr = np.array(vectors)
        return arr[0] if single else arr


class _FakeCrossEncoder:
    def __init__(self, *args, **kwargs):
        pass

    def predict(self, pairs, **kwargs):
        return [float(len(str(p))) for p in pairs]


fake_module = types.ModuleType("sentence_transformers")
fake_module.SentenceTransformer = _FakeSentenceTransformer
fake_module.CrossEncoder = _FakeCrossEncoder
sys.modules["sentence_transformers"] = fake_module


class _FakeOllamaProvider:
    """
    Minimal stand-in for Core.ollama_provider.OllamaProvider —
    returns a syntactically-valid canned Playwright body / plain text
    depending on what the prompt looks like, so
    TestExecutionManager.generate_automation()'s real
    assembly/validation/repair-loop code actually runs against
    *something* parseable, without ever reaching a real model.
    """

    def __init__(self, *args, **kwargs):
        pass

    @property
    def provider_name(self):
        return "fake-ollama-test-only"

    def generate(self, prompt, temperature=0.2, max_tokens=1500, **kwargs):
        if "flat list of simple Python statements" in prompt:
            body = (
                "page.goto('http://127.0.0.1:8199')\n"
                "page.wait_for_selector('#login-heading')\n"
                "assert page.locator('#login-heading').is_visible()"
            )
            return {"success": True, "response": body}

        if "Use Python's 'requests' library" in prompt:
            body = (
                "import requests\n\n"
                "# TEST-ONLY fake AI script — not real generated quality,\n"
                "# only long enough to pass generate_automation()'s\n"
                "# minimum-length sanity check.\n"
                "response = requests.get('http://127.0.0.1:8199/')\n"
                "assert response.status_code == 200, f'Unexpected status: {response.status_code}'\n"
                "print('API TEST PASSED')\n"
            )
            return {"success": True, "response": body}

        if "suggested_type" in prompt:
            return {
                "success": True,
                "response": '{"suggested_type": "Playwright", "reason": "TEST-ONLY fake suggestion."}',
            }

        if "Git commit message" in prompt:
            return {"success": True, "response": "Update files (TEST-ONLY fake commit message)"}

        return {"success": True, "response": "TEST-ONLY fake response."}

    def is_available(self):
        return True


import Core.ollama_provider as ollama_provider_module  # noqa: E402
ollama_provider_module.OllamaProvider = _FakeOllamaProvider

import uvicorn  # noqa: E402
from Web.main import app  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8100
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
