from abc import ABC, abstractmethod
from app.config import get_settings

class BaseAIClient(ABC):
    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Send a prompt, return a string response."""
        ...

class AnthropicClient(BaseAIClient):
    def __init__(self):
        import anthropic
        settings = get_settings()
        self._client = anthropic.AsyncAnthropic(api_key=settings.active_llm_api_key)
        self._model = settings.LLM_MODEL

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        return message.content[0].text

class OpenAIClient(BaseAIClient):
    def __init__(self):
        from openai import AsyncOpenAI
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.active_llm_api_key)
        self._model = settings.LLM_MODEL

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )
        return response.choices[0].message.content

class GoogleClient(BaseAIClient):
    def __init__(self):
        from google import genai
        settings = get_settings()
        self._client = genai.Client(api_key=settings.active_llm_api_key)
        self._model = settings.LLM_MODEL

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        from google.genai import types
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
            )
        )
        return response.text

def get_ai_client() -> BaseAIClient:
    """Factory — returns the correct client based on LLM_PROVIDER env var."""
    provider = get_settings().LLM_PROVIDER
    clients = {
        "anthropic": AnthropicClient,
        "openai": OpenAIClient,
        "google": GoogleClient,
    }
    if provider not in clients:
        raise ValueError(f"Unknown LLM provider: {provider}. Choose from {list(clients.keys())}")
    return clients[provider]()
