from openai import OpenAI

from llms.base_llm import BaseLLM
from config.config import get_deepseek_api_key


class DeepSeekWrapper(BaseLLM):
    """Wrapper class for the DeepSeek API to handle job description analysis."""

    def __init__(self):
        """Initialize the DeepSeek client with API key and base URL."""
        super().__init__()
        self.client = OpenAI(
            api_key=get_deepseek_api_key(), base_url="https://api.deepseek.com"
        )

    def invoke(
        self,
        prompt,
        model_type="deepseek-chat",
        temperature=0.35,
        max_tokens=2048,
        top_p=0.95,
        frequency_penalty=1.05,
        stream=False,
    ):
        """Send a prompt to the DeepSeek API and return the response.

        Args:
            prompt (str): The prompt to send to the API

        Returns:
            str: The generated response from the API
        """
        response = self.client.chat.completions.create(
            model=model_type,  # Alternative: "deepseek-reasoner"/"deepseek-chat"
            messages=[
                {"role": "system", "content": "You are a helpful assistant"},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            # max_tokens=max_tokens,
            # top_p=top_p,
            # frequency_penalty=frequency_penalty,
            stream=stream,
        )
        # reasoning_content = response.choices[0].message.reasoning_content
        response_content = response.choices[0].message.content
        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        return response_content, input_tokens, output_tokens
