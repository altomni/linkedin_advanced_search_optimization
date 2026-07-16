from __future__ import annotations
import os

from dotenv import load_dotenv
from openai import OpenAI, AsyncOpenAI
import httpx


import re, json, time, logging, random
from typing import Any, Dict, List, Optional, Callable, Tuple

import openai

from llms.base_llm import BaseLLM
from config.config import get_openai_api_key, get_openai_base_url, OPENAI_API_KEYS

load_dotenv()

# Configure httpx logging to capture retry details
logging.getLogger("httpx").setLevel(logging.DEBUG)  # Enable DEBUG to see retry reasons

# Minimum max_completion_tokens for gpt-5 family + reasoning. `reasoning_effort`
# consumes a large chunk of the budget for hidden reasoning tokens before any
# visible output is produced; the wrapper's old defaults (4096 sync / 2048 async)
# routinely trip the API's "Could not finish the message because max_tokens or
# model output limit was reached" 400 with medium effort. Bump the floor so
# there's room for reasoning + the actual answer.
_GPT5_REASONING_MIN_TOKENS = 8192


class ChatGPTWrapper(BaseLLM):

    def __init__(
        self,
        max_retries: int = 0,  # Default 0 = backward compatible (no retry)
        retry_delay: float = 1.0,
        backoff_factor: float = 1.5
    ):
        super().__init__(max_retries, retry_delay, backoff_factor)
        _base_url = get_openai_base_url()

        # Async client shares one HTTP/2 connection (safe: single event loop)
        # Sync clients do NOT share httpx.Client — each gets the SDK default pool
        # (sharing one httpx.Client across threads causes HTTP/2 frame corruption → Cloudflare 400)
        self._async_http = httpx.AsyncClient(http2=True)

        if OPENAI_API_KEYS and not _base_url:
            # Multi-key mode: pre-build a client pool, random pick per request
            key_suffixes = [k[-6:] for k in OPENAI_API_KEYS]
            print(f"[ChatGPTWrapper] Multi-key mode: {len(OPENAI_API_KEYS)} keys (..{', ..'.join(key_suffixes)}) | HTTP/2: OFF(sync) ON(async)")
            self._sync_clients = [
                OpenAI(api_key=k, base_url=_base_url)
                for k in OPENAI_API_KEYS
            ]
            self._async_clients = [
                AsyncOpenAI(api_key=k, base_url=_base_url, max_retries=3, http_client=self._async_http)
                for k in OPENAI_API_KEYS
            ]
        else:
            # Single-key or LiteLLM mode
            _api_key = get_openai_api_key()
            _mode = "LiteLLM proxy" if _base_url else "Single-key"
            print(f"[ChatGPTWrapper] {_mode} mode: key=..{(_api_key or '')[-6:]} | base_url={_base_url or 'OpenAI default'} | HTTP/2: OFF(sync) ON(async)")
            self._sync_clients = [OpenAI(api_key=_api_key, base_url=_base_url)]
            self._async_clients = [AsyncOpenAI(api_key=_api_key, base_url=_base_url, max_retries=3, http_client=self._async_http)]

    @property
    def client(self):
        """Random pick a sync client per call."""
        c = random.choice(self._sync_clients)
        if len(self._sync_clients) > 1:
            print(f"[ChatGPTWrapper] sync call using key=..{c.api_key[-6:]}")
        return c

    @property
    def async_client(self):
        """Random pick an async client per call."""
        c = random.choice(self._async_clients)
        if len(self._async_clients) > 1:
            print(f"[ChatGPTWrapper] async call using key=..{c.api_key[-6:]}")
        return c

    def invoke(
        self,
        prompt,
        model_type="gpt-4.1",
        temperature=0.7,
        max_tokens=4096,
        validator: Optional[Callable[[str, Dict], bool]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        max_retries: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ):
        """
        Synchronous invocation with optional retry and validation.

        Fully backward compatible: if validator and metadata are not provided,
        behaves exactly as before.

        Args:
            prompt: Prompt text
            model_type: Model name
            temperature: Temperature
            max_tokens: Max tokens
            validator: Optional validation function from retry_validator.py
            metadata: Metadata for validation
            max_retries: Override instance max_retries for this call

        Returns:
            Tuple of (response, input_tokens, output_tokens)

        Examples:
            # Old usage (backward compatible)
            response, in_tok, out_tok = llm.invoke(prompt)

            # New usage (with validation)
            response, in_tok, out_tok = llm.invoke(
                prompt,
                validator=my_validator,
                max_retries=2,
            )
        """
        # Adjust temperature for gpt-5: the gpt-5 family only accepts the default
        # temperature of 1.0 and returns a 400 ("Unsupported value: 'temperature'
        # does not support X ...") for anything else, so clamp it here.
        temp = 1.0 if "gpt-5" in model_type else temperature

        # Default reasoning_effort to "medium" for gpt-5 / 5+ models.
        # Non-reasoning models reject the param, so only pass it for the gpt-5 family.
        effective_reasoning_effort = reasoning_effort
        if effective_reasoning_effort is None and "gpt-5" in model_type:
            effective_reasoning_effort = "medium"

        # Reasoning models consume hidden thinking tokens before producing
        # visible output; floor the completion budget so reasoning doesn't
        # eat the entire allowance and surface as a 400 / empty response.
        effective_max_tokens = max_tokens
        if effective_reasoning_effort is not None and "gpt-5" in model_type:
            effective_max_tokens = max(max_tokens, _GPT5_REASONING_MIN_TOKENS)

        # Define the actual API call function
        def _call_api():
            create_kwargs = dict(
                model=model_type,  # also: gpt-4.1-mini, gpt-4-turbo, gpt-3.5-turbo
                messages=[
                    {"role": "system", "content": "You are a concise assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=temp,
                max_completion_tokens=effective_max_tokens,
            )
            if effective_reasoning_effort is not None and "gpt-5" in model_type:
                create_kwargs["reasoning_effort"] = effective_reasoning_effort
            chat = self.client.chat.completions.create(**create_kwargs)
            input_tokens = chat.usage.prompt_tokens
            output_tokens = chat.usage.completion_tokens
            return chat.choices[0].message.content, input_tokens, output_tokens

        # Decide whether to use retry mechanism
        effective_retries = max_retries if max_retries is not None else self.max_retries
        if effective_retries > 0 or validator is not None:
            # Temporarily override max_retries if caller specified
            original = self.max_retries
            if max_retries is not None:
                self.max_retries = max_retries
            try:
                return self._invoke_with_retry(_call_api, validator, metadata)
            finally:
                self.max_retries = original
        else:
            # Direct call (backward compatible)
            return _call_api()

    async def ainvoke(
        self,
        prompt,
        model_type="gpt-4.1",
        temperature=0.7,
        max_tokens=2048,
        validator: Optional[Callable[[str, Dict], bool]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        max_retries: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ):
        """
        Asynchronous invocation with optional content-level retry and validation.

        Backward compatible: without validator/max_retries, behaves exactly as before.

        Args:
            prompt: Prompt text
            model_type: Model name
            temperature: Temperature
            max_tokens: Max tokens
            validator: Optional validation function (raw_output, metadata) -> bool
            metadata: Metadata to pass to validator
            max_retries: Override instance max_retries for this call

        Returns:
            Tuple of (response, input_tokens, output_tokens)
        """
        # gpt-5 family only accepts temperature=1.0 (see sync invoke); clamp it.
        if "gpt-5" in model_type:
            temperature = 1.0

        # Default reasoning_effort to "medium" for gpt-5 / 5+ models.
        # Non-reasoning models reject the param, so only pass it for the gpt-5 family.
        effective_reasoning_effort = reasoning_effort
        if effective_reasoning_effort is None and "gpt-5" in model_type:
            effective_reasoning_effort = "medium"

        # See sync invoke: reasoning models need a higher floor or they return
        # an empty visible response (whole budget spent on hidden reasoning).
        effective_max_tokens = max_tokens
        if effective_reasoning_effort is not None and "gpt-5" in model_type:
            effective_max_tokens = max(max_tokens, _GPT5_REASONING_MIN_TOKENS)

        async def _call_api():
            try:
                create_kwargs = dict(
                    model=model_type,
                    messages=[
                        {"role": "system", "content": "You are a concise assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_completion_tokens=effective_max_tokens,
                )
                if effective_reasoning_effort is not None and "gpt-5" in model_type:
                    create_kwargs["reasoning_effort"] = effective_reasoning_effort
                chat = await self.async_client.chat.completions.create(**create_kwargs)
                input_tokens = chat.usage.prompt_tokens
                output_tokens = chat.usage.completion_tokens
                return chat.choices[0].message.content, input_tokens, output_tokens
            except Exception as e:
                error_type = type(e).__name__
                error_details = []
                if hasattr(e, 'status_code'):
                    error_details.append(f"HTTP {e.status_code}")
                if hasattr(e, 'code'):
                    error_details.append(f"code={e.code}")
                if hasattr(e, 'type'):
                    error_details.append(f"type={e.type}")
                if hasattr(e, 'message'):
                    error_details.append(f"msg={e.message[:100]}")
                error_info = f"{error_type}" + (f" ({', '.join(error_details)})" if error_details else "")
                logging.error(f"🚨 OpenAI API Error: {error_info}")
                raise

        # Decide whether to use retry mechanism
        if max_retries is not None or validator is not None:
            return await self._ainvoke_with_retry(
                _call_api, validator=validator, metadata=metadata,
                max_retries=max_retries if max_retries is not None else 2,
            )
        else:
            return await _call_api()


_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


class ChatGPTJSONWrapper:
    """
    A thin helper that:
      1. Forces ChatGPT to answer with a markdown ```json``` block.
      2. Extracts & deserialises that block.
      3. Retries up to `max_retries` times (default 3) on extraction errors.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.5,
        max_retries: int = 3,
        logger: Optional[logging.Logger] = None,
        **client_kwargs,
    ) -> None:
        self.model = model
        self.temp = temperature
        self.max_retries = max_retries
        self.log = logger or logging.getLogger(__name__)
        # Route through LiteLLM proxy if configured
        if 'api_key' not in client_kwargs:
            client_kwargs['api_key'] = get_openai_api_key()
        if 'base_url' not in client_kwargs and get_openai_base_url():
            client_kwargs['base_url'] = get_openai_base_url()
        openai_client = openai.OpenAI(**client_kwargs)
        self._chat = openai_client.chat.completions

        # System prompter that enforces JSON‑only response
        self._system_msg = {
            "role": "system",
            "content": (
                "You are a strict JSON generator. "
                "Reply ONLY with a valid JSON wrapped in ```json``` fenced block. "
                "Do not include any other text."
            ),
        }

    def invoke(self, prompt: str, response_mode="text", **kwargs) -> Any:
        """
        Sends `prompt` to ChatGPT and returns the parsed JSON.
        Raises RuntimeError after `max_retries` failures.
        """

        if "json" in response_mode.lower().strip():

            for attempt in range(1, self.max_retries + 1):
                self.log.debug("LLM attempt %d/%d", attempt, self.max_retries)
                raw = self._call(
                    [self._system_msg, {"role": "user", "content": prompt}], **kwargs
                )
                try:
                    return self._extract_json(raw)
                except ValueError as e:
                    self.log.warning("JSON parse failed: %s", e)
                    if attempt == self.max_retries:
                        raise RuntimeError(
                            f"Failed after {self.max_retries} tries.\nLast reply:\n{raw}"
                        ) from e
                    time.sleep(attempt)
        else:
            raw = self._call(
                [self._system_msg, {"role": "user", "content": prompt}], **kwargs
            )
            return raw

    def _call(self, messages: List[Dict[str, str]], **kwargs) -> str:
        resp = self._chat.create(
            model=self.model,
            messages=messages,
            temperature=self.temp,
            **kwargs,
        )
        return resp.choices[0].message.content

    @staticmethod
    def _extract_json(text: str) -> Any:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            raise ValueError("No ```json``` block found.")
        return json.loads(match.group(1))


# if __name__ == "__main__":
#     logging.basicConfig(level=logging.INFO)
#
#     wrapper = ChatGPTJSONWrapper()
#
#     try:
#         result = wrapper.invoke(
#             "Give me a summary object for the phrase 'Paris in spring' with keys city and season.",
#             "json",
#         )
#         print(result)
#         # ➜ {'city': 'Paris', 'season': 'spring'}
#     except RuntimeError as err:
#         print("Wrapper failed:", err)


if __name__ == "__main__":
    llm = ChatGPTWrapper()

    jd = """
    "Job Description:
ESSENTIAL DUTIES AND RESPONSIBILITIES: 1. Organize the business logic of the MYA portal website to provide users with a convenient and effective userexperience. 2. Create product requirement documents, design a business prototype diagram for the portal website, collaboratewith UI design, testing, and website development personnel to ensure that the product is launched according todesign requirements. 3. Translate product strategy into detailed requirements for prototyping and final development by engineeringteams. 4.Collaborate closely with engineering, production, marketing in the development.
Job Summary:
About MYA: MYA (www.myacards.com) is a natural history trading card game (TCG) startup that uses blockchain technology tocreate unique cards that exists both physically and digitally. At MYA, it is our mission to create a vibrant ecosystem and community for our game where the best qualities ofTCG‚Äôs are complemented by cutting edge, high-impact digital technologies. We invent a new strategy game where users play with decks of our physical cards, while also offering users anonline platform to interact with digital card collections. Through our online capabilities, we aspire to stand out asa distinguished company that maximizes value for TCG gamers and collectors alike. What are we going to do: We are looking for a qualified candidate to join our startup‚Äôs founding team, in alignment with our MYA values, weare committed to cultivating a passionate work environment for all employees to positively impact our cultureevery day. Required Position: Backend ManagerEmployment type: Part-Time or Full-Time Job Description:
Job Requirements:
KNOWLEDGE, SKILLS, AND ABILITIES: 1.Two or more years in back-end development. 2.Experience in building functional and effective platforms. 3.Fluency in JavaScript, HTML5 and CSS. 4.Proficient backend debugging skills, conduct in-depth analysis and optimization of the performance andarchitecture of the project back-end, and improve back-end performance and reliability. 5.Fluent proficiency in English and Mandarin Chinese. EDUCATION/YEARS EXPERIENCE: 1.Bachelor‚Äôs degree (or equivalent) in computer science or related field. 2.Strong interpersonal and communication skills.
Job Notes:
['Backend Manager:\n- FTE, permanent. This is the priority \n- Fully remote, can be anywhere in the U.S.\n- Should avoid candidates who are asking for higher end salaries. This is a very early stage startup company, so they will only get salary for now. \n- Solid grasp of Java.\n- No set budget in mind right now. They just need to see what the market looks like and prefer to hire someone with a trading card background \n- Since they have team members in China that are not great with English, someone who is able to speak Mandarin would be a big plus in order to communicate with those team members\n- Interview Process: Henry will do the first round screening, 2nd round will have their Technology Advisor to understand their level of software skills, and then 3rd round will be final discussion.\n- Need to be hired by the end of December', '<p>New Requirement:<br /><br />Candidates must have US Citizenship or Greencard holder.&nbsp;</p>', '<p>Java spring boot exp is important for this role</p>']
"
    """


    prompt = f"""Why this attached job info job function is considered as [['Product Management', 0.7], ['Information Technology', 0.3]], 
    but not “Engineering”, "Program and Project Management".
    
    
    Attached job info:
    {jd}
    """

    response = llm.invoke(prompt)
    from pprint import pprint
    print(response[0])
