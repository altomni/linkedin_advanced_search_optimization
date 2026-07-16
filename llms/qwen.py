import time

from dotenv import load_dotenv
from openai import OpenAI
from dashscope import Generation

from llms.base_llm import BaseLLM
from config.config import get_dashscope_api_key

load_dotenv()


# pip install -U dashscope
#
class QwenWrapper(BaseLLM):
    def __init__(self):
        super().__init__()
        self.client = OpenAI(
            # 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx",
            api_key=get_dashscope_api_key(),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def invoke(
        self,
        prompt,
        model_type="qwen-plus",
        temperature=0.35,
        max_tokens=2048,
        top_p=0.95,
        enable_thinking=False,
        thinking_budget=500,
    ):
        # completion = self.client.chat.completions.create(
        #     # 模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
        #     model="qwen-plus-latest",
        #
        #     messages=[
        #         {"role": "system", "content": "You are a helpful assistant."},
        #         {"role": "user", "content": prompt},
        #     ],
        #     # Qwen3模型通过enable_thinking参数控制思考过程（开源版默认True，商业版默认False）
        #     # 使用Qwen3开源版模型时，若未启用流式输出，请将下行取消注释，否则会报错
        #     # extra_body={"enable_thinking": True}
        # )
        # response = completion.choices[0].message.content

        messages = [{"role": "user", "content": prompt}]
        extra = {"enable_thinking": True, "thinking_budget": thinking_budget}

        start_time = time.time()
        output_message = ""
        total_input_tokens = 0
        total_output_tokens = 0
        for chunk in Generation.call(
            model="qwen3-235b-a22b",  # any id from the list below
            messages=messages,
            temperature=temperature,
            result_format="message",  # required for Qwen3
            stream=True,  # Qwen3 *requires* stream mode
            incremental_output=True,  # ditto
            enable_thinking=enable_thinking,
            extra_body=extra,
        ):
            output_message += chunk.output.choices[0].message.content
            total_input_tokens += chunk.usage.input_tokens
            total_output_tokens += chunk.usage.output_tokens

        # print(resp.output.choices[0].message.content)
        print(f"Qwen3 model time: {time.time() - start_time:.2f}s")
        print(f"Qwen3 model input tokens: {total_input_tokens}")
        print(f"Qwen3 model output tokens: {total_output_tokens}")
        return output_message, total_input_tokens, total_output_tokens


if __name__ == "__main__":
    qwen_wrapper = QwenWrapper()
    response = qwen_wrapper.invoke("What is Qwen?")
    print(response)
