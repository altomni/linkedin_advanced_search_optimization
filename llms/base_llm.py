import time
import asyncio
import logging
from typing import Optional, Callable, Tuple, Dict, Any

logger = logging.getLogger(__name__)


class BaseLLM:
    def __init__(
        self,
        max_retries: int = 0,  # Default 0 = backward compatible (no retry)
        retry_delay: float = 1.0,
        backoff_factor: float = 1.5
    ):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.backoff_factor = backoff_factor

    def invoke(
        self,
        prompt,
        model_type,
        temperature=0.35,
        max_tokens=2048,
        top_p=0.95,
        enable_thinking=False,
        thinking_budget=500,
    ):
        pass

    def _invoke_with_retry(
        self,
        invoke_func: Callable[[], Tuple[str, int, int]],
        validator: Optional[Callable[[str, Dict], bool]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, int, int]:
        """
        Internal retry wrapper with validation support.

        This method reuses validation logic from retry_validator.py!

        Args:
            invoke_func: Function that calls LLM API, returns (response, input_tokens, output_tokens)
            validator: Optional validation function (raw_output, metadata) -> bool
            metadata: Metadata to pass to validator

        Returns:
            Tuple of (response, input_tokens, output_tokens)
        """
        last_exception = None
        delay = self.retry_delay
        total_input_tokens = 0
        total_output_tokens = 0
        last_response = None

        for attempt in range(self.max_retries + 1):
            try:
                # Call the actual LLM API
                response, input_tokens, output_tokens = invoke_func()
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens
                last_response = response

                # If no validator, return immediately (exception-only retry)
                if validator is None:
                    if attempt > 0:
                        logger.info(f"✓ Retry succeeded on attempt {attempt + 1}/{self.max_retries + 1}")
                    return response, total_input_tokens, total_output_tokens

                # Validate response using validator from retry_validator.py
                if validator(response, metadata or {}):
                    # Validation passed
                    if attempt > 0:
                        logger.info(f"✓ Retry succeeded on attempt {attempt + 1}/{self.max_retries + 1}")
                    return response, total_input_tokens, total_output_tokens
                else:
                    # Validation failed
                    logger.warning(
                        f"✗ Validation failed on attempt {attempt + 1}/{self.max_retries + 1}. "
                        f"Response preview: {response[:200]}..."
                    )

                    if attempt < self.max_retries:
                        logger.info(f"Retrying in {delay:.1f}s...")
                        time.sleep(delay)
                        delay *= self.backoff_factor
                    else:
                        # Max retries reached, still return response
                        logger.error(f"✗ Max retries ({self.max_retries}) reached. Returning last response.")
                        return response, total_input_tokens, total_output_tokens

            except Exception as e:
                last_exception = e
                logger.error(f"✗ Exception on attempt {attempt + 1}/{self.max_retries + 1}: {str(e)}")

                if attempt < self.max_retries:
                    logger.info(f"Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                    delay *= self.backoff_factor
                else:
                    logger.error(f"✗ Max retries ({self.max_retries}) reached. Raising exception.")
                    raise last_exception

        # Should never reach here
        if last_exception:
            raise last_exception
        return last_response, total_input_tokens, total_output_tokens

    async def _ainvoke_with_retry(
        self,
        ainvoke_func: Callable,
        validator: Optional[Callable[[str, Dict], bool]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        max_retries: Optional[int] = None,
    ) -> Tuple[str, int, int]:
        """
        Async retry wrapper with validation support (mirrors _invoke_with_retry).

        Args:
            ainvoke_func: Async function that calls LLM API, returns (response, input_tokens, output_tokens)
            validator: Optional validation function (raw_output, metadata) -> bool
            metadata: Metadata to pass to validator
            max_retries: Override self.max_retries for this call

        Returns:
            Tuple of (response, input_tokens, output_tokens)
        """
        retries = max_retries if max_retries is not None else self.max_retries
        last_exception = None
        delay = self.retry_delay
        total_input_tokens = 0
        total_output_tokens = 0
        last_response = None

        for attempt in range(retries + 1):
            try:
                response, input_tokens, output_tokens = await ainvoke_func()
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens
                last_response = response

                if validator is None:
                    if attempt > 0:
                        logger.info(f"✓ Async retry succeeded on attempt {attempt + 1}/{retries + 1}")
                    return response, total_input_tokens, total_output_tokens

                if validator(response, metadata or {}):
                    if attempt > 0:
                        logger.info(f"✓ Async retry succeeded on attempt {attempt + 1}/{retries + 1}")
                    return response, total_input_tokens, total_output_tokens
                else:
                    logger.warning(
                        f"✗ Async validation failed on attempt {attempt + 1}/{retries + 1}. "
                        f"Response preview: {response[:200]}..."
                    )
                    if attempt < retries:
                        logger.info(f"Retrying in {delay:.1f}s...")
                        await asyncio.sleep(delay)
                        delay *= self.backoff_factor
                    else:
                        logger.error(f"✗ Async max retries ({retries}) reached. Returning last response.")
                        return response, total_input_tokens, total_output_tokens

            except Exception as e:
                last_exception = e
                logger.error(f"✗ Async exception on attempt {attempt + 1}/{retries + 1}: {str(e)}")
                if attempt < retries:
                    logger.info(f"Retrying in {delay:.1f}s...")
                    await asyncio.sleep(delay)
                    delay *= self.backoff_factor
                else:
                    logger.error(f"✗ Async max retries ({retries}) reached. Raising exception.")
                    raise last_exception

        if last_exception:
            raise last_exception
        return last_response, total_input_tokens, total_output_tokens
