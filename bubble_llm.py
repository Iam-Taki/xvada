#from openai import OpenAI
#client = OpenAI()

# You are an AI assistant. You will help to build parse trees from flat tree levels.
#     You will combine adjacent nodes from a tree level. Those nodes will be placed under a new parent node at that position.
#     You will suggest node groups in a way that will build the complete parse tree iteratively.
#     Input: A list of tree levels separated by square brackets, the nodes in a level are separated by commas.
#     Output: A list of unique groups as json, the format should be json[siblings]:[[node1, node2, ...],...group_n].
#     The workflow should be:
#     - Break an input level into smaller independent units, remember there will be recursive structure.
#     - Think what smallest units represent a language construct, free of recursion.
#     - The group will create a new level at that position in the tree.
#     - The group should represent a grammar rule of the language.
#     - Remember recursive expansion makes the tree level long, don't suggest long groups. 
#     - A group can be as small as two tokens only.
#     - A level can be concatenation of multiple statments, suggest groups that represent a single statement.
#     - Limit the group list to best **20** suggestions. Never more than that.

system_prompt = ["""
    You are assisting a grammar inference system. Your task is to propose 
    small structural groupings (“bubbles”) from a list of *flat tree levels*. 
    Each bubble groups a contiguous set of adjacent nodes under a new parent node.

    Your suggestions will be used to build complete parse trees *iteratively*.

    INPUT:
    - A list of tree levels enclosed in square brackets.
    - Each level contains nodes separated by commas.
    - Consider all levels, and output bubbles from any level.

    OUTPUT:
    - A JSON object with one key, "siblings", whose value is a list of groups.
    - Each group is a list of adjacent node names, e.g.:
      {"siblings": [["x", "=", "y"], ["y", "+", "3"], ...]}

    TASK RULES:
    1. Propose only **contiguous** groups (no skipping or reordering).
    2. Identify *small*, recursion-free semantic units, atomic expressions or sub-expressions.
    3. Groups should be meaningful language constructs (e.g., statements, expressions, blocks for programming languages).
    4. Avoid long groups: recursion expands levels, so long spans are unlikely to be correct. Favor short, compositional groupings (as small as 2 nodes).
    5. Do NOT introduce new tokens, rename nodes, or invent structure.
    6. A level may contain multiple concatenated statements—group within each.
    7. Return **at most 20 high-confidence groups**. Never more.
    8. Expressions may be unary, binary, or n-ary; groupings should reflect the correct arity of each expression.
    9. Do not output duplicate groups or groups that span an entire level unless it is a minimal construct.

    GOAL:
    Suggest structures that help gradually reconstruct the parse tree. Focus on atomic, meaningful, and compositional units that reflect the underlying grammar.
"""]


system_message = [{'role': 'system', 'content': system_prompt[0]}]
# i =1
# while i < len(system_prompt):
#     messages.append({'role': 'system', 'name': 'example_tree_levels', 'content': system_prompt[i]})
#     messages.append({'role': 'system', 'name': 'example_sibling_group', 'content': system_prompt[i+1]})
#     i += 2
# messages.append({'role': 'system', 'content': 'Given some flat tree levels, suggest unique groups to build the parse trees. Discard long groups (len>10) from output, do not show too many groups (limit output within 10 groups). Refine your suggestions based on the feedback after each iteration. \
#                  Only show list of siblings as json output. The format should be json[siblings]:[[node1, node2, ...],...]'})
chat_log = []
old_trees = []
def bubble_api(trees):
    # global chat_log
    # if feedback:
    #     chat_log.append({'role': 'system', 'name': 'feedback', 'content': f"Following suggestions were applied to the trees. Get hint from these groups to find similar patterns. \
    #                      Trim long inputs and focus on the smaller recursion-free units (within 2-10 tokens).\n{feedback}"})
        # chat_log.append({'role': 'system', 'name': 'instruction', 'content': "Rethink the suggestions. You might be missing shorter constructs those are hidden within the incorrect suggestions."})
    # elif chat_log:
    #     chat_log.append({'role': 'system', 'name': 'feedback', 'content': f"None of the suggested group at last turn is valid. Don't repeat those. \
    #                      Look for common language concepts, considering diverse lengths within 2-10 tokens, don't output an entire level from input."})
    # chat_log.append({'role': 'user', 'content': f'{trees}'})
    # example groupings

    # if old_bubbles:

    #     old_bubble_str = '\n'.join(
    #         str([elem.payload for elem in bubble.bubbled_elems])
    #         for bubble in old_bubbles
    #     )
    #     old_examples = [{'role': 'system', 'name': 'example_groups', 
    #                            'content': f"Here are some groups that were already applied to the trees:\n{old_bubble_str}"}]
    
    chat_log = [{'role': 'user', 'content': f'{trees}'}]
    global old_trees
    prompt = system_message + old_trees + chat_log
    gpt = client.chat.completions.create(
        model="gpt-4o",
        messages=prompt,
        seed = 101,
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=1000
    )
    response = gpt.choices[0].message.content
    print(response)
    
    old_trees = [{'role': 'system', 'name': 'tree_transformation_history',
                               'content': f'{trees}'}]
    return response

if __name__ == '__main__':
    print("Welcome to the interactive GPT chat. Type 'quit' to exit.")
        
    chat_log = []
    start = len(system_message)
    while True:
        user_prompt = input("You: ")
        if user_prompt.lower() == 'quit':
            break
        chat_log.append({'role': 'user', 'content': user_prompt})
        response = bubble_api(chat_log)
        print(f"AI: {response}")
        chat_log.append({'role': 'assistant', 'content': response})
        if len(chat_log) > 10:
            chat_log.pop(0)
            chat_log.pop(0)

    # chat_log = system_prompt
    # while True:
    #     user_prompt = input("You: ")
    #     if user_prompt.lower() == 'quit':
    #         break
    #     response, chat_log = chat_with_gpt(user_prompt, chat_log)
    #     print(f"AI: {response}")

