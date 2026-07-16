import ast
from urllib.parse import quote

from llms.chatgpt import ChatGPTWrapper
from llms.deepseek import DeepSeekWrapper


def single_filter_to_str(single_filter: dict) -> str:
    # print("single_filter:", single_filter)
    filters_str = ""
    filter_type = single_filter["type"]
    filters_str += f"type:{filter_type},"
    sub_str_list = []
    for value in single_filter["values"]:
        formatted_text = quote(value["text"])
        if "id" in value.keys():
            raw_value_id = value["id"]
            # "urn%3Ali%3Aorganization%3A"

            value_id = (
                "urn%3Ali%3Aorganization%3A" + str(raw_value_id)
                if "COMPANY".lower() in filter_type.lower()
                else raw_value_id
            )
            if "company" in filter_type.lower():
                sub_str = (
                    "(id:"
                    + value_id
                    + ","
                    + f"text:{formatted_text},selectionType:{value['selectionType']},parent:(id:0))"
                )
            else:
                sub_str = (
                    "(id:"
                    + value_id
                    + ","
                    + f"text:{formatted_text},selectionType:{value['selectionType']})"
                )

        else:
            sub_str = f"(text:{formatted_text},selectionType:{value['selectionType']})"
        sub_str_list.append(sub_str)
    sub_str = ",".join(sub_str_list)
    filters_str += "values:List(" + sub_str + ")"
    filters_str = "(" + filters_str + ")"

    return filters_str


def multi_filters_to_str(sales_nav_filters: list) -> str:
    filters_str = ""
    filters_str_list = []
    for idx, single_filter in enumerate(sales_nav_filters):
        sub_filters_str = single_filter_to_str(single_filter)
        filters_str_list.append(sub_filters_str)
        filters_str += sub_filters_str + ","

    filters_str = ",".join(filters_str_list)
    final_filters_str = "List(" + filters_str + ")"

    # print("final_filters_str:", final_filters_str)

    final_filters_str = final_filters_str.replace(
        "#special-id-term#", quote("urn%3Ali%3Aorganization%3A", safe="%")
    )

    return final_filters_str



    # 2. With each location, give several common used synonyms of the same location.
    # Example Input: "San Francisco, CA"
    # Example Output: ["San Francisco, CA", "Greate San Francisco area", "San Francisco Bay Area", "Bay Area"]
def convert_location_list(llm, location_str, temperature=0.5):
    prompt = f"""
    Please extract the provided location string to location list.
    Example Input: "Remote(US, Canada, China)"        
    Example Output: ["US", "Canada", "China"]
    Example Input: "Bay Area, California"
    Example Output: ["Bay Area CA"]

    More specific rules:
    If the location is city level, expand to local nearby area, and keep both
    Example Input: "Palo Alto, California"
    Example Output: ["Palo Alto CA"，"San Francisco Bay Area CA"]
    Example Input: "Mountain View"
    Example Output: ["Mountain View CA", "San Francisco Bay Area"]
    Example Input: "San Francisco, CA, USA"
    Example Output: ["San Francisco Bay Area CA", "USA"]

    Your output should be a list of strings. Do not give any extra information. Do not give opening or closure of your output.

    Example Input: "Remote(US, Canada, China)"        
    Example Output: ["US", "Canada", "China"]

    ---

    Provided location string: {location_str}    
    """
    response, input_tokens, output_tokens = llm.invoke(prompt, temperature=temperature)
    location_list = ast.literal_eval(response)
    return location_list, input_tokens, output_tokens



if __name__ == "__main__":
    llm = ChatGPTWrapper()
    test_list = ["Remote(US, Canada, China)",
                 "Mountain View, CA; Remote in USA",
                 "Mountain View, CA",
                 "Greate Boston Area",
                 "Remote, San Mateo, United States, Canada",
                 "Singapore or Remote (Singapore working hours)",
                 "Sunnyvale, CA, Pittsburgh PA, Remote",
                 ]

    for test_str in test_list:
        print("test_str:", test_str)
        location_list = convert_location_list(llm, test_str)
        print(location_list)