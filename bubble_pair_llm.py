#from openai import OpenAI
#client = OpenAI()

# You are an AI assistant. You will be given a list of tree levels.
# You will find out segment pairs from these levels in a way that a segment pair can be derived from the same AST node.
# Input: A list of tree levels separated by square brackets, the nodes in a level are separated by commas.
# Output: A list of pairs as json, the format should be json[siblings]:[[pair1], [pair2],...[pair_n]]. Each pair should be two list of sibling nodes.
# - Each pair should contain two **different** lists of nodes, these nodes should represent identical AST parents.
# - Remeber there could be recursive structure, suggest the smallest recursion free units.
# - Discard if two lists in a pair are exactly the same.
# - Limit the list to best 20 suggestions.

system_prompt = ["""You are assisting a grammar inference system. 
                Your task is to identify *pairs of sibling-node segments* across the given 
                tree levels that could have been derived from the *same AST parent node*.

                INPUT:
                - A list of tree levels enclosed in square brackets.
                - Nodes within a level are separated by commas.
                - Consider all levels, and output pairs of segments.

                OUTPUT:
                Return a JSON object of the form:
                {"siblings": [ [segmentA, segmentB], [segmentC, segmentD], ... ] }

                Where:
                - Each segment is a list of adjacent sibling nodes from a level.
                - Each pair contains **two different segments**.
                - A segment pair represents two sibling groups that likely share the same AST parent.

                RULES:
                1. Segments must be **contiguous** subsequences from a level (no skipping or reordering).
                2. Each pair must contain two **distinct** segments (discard identical duplicates).
                3. Segments should be **small, recursion-free units** (due to tree expansion).
                Typical sizes: 2–5 nodes.
                4. Segments in a pair should be **structurally similar**, suggesting they come 
                from the same grammar construct (e.g., two expressions, two assignments, 
                two operator patterns, two clauses).
                5. Expressions may be unary, binary, or n-ary; 
                 segment groupings should reflect the correct arity of each expression.
                6. Do NOT invent tokens, rename nodes, or infer new syntax.
                7. Return only the **best 20 pairs** (never more).

                GOAL:
                Identify pairs of sibling-node segments that could plausibly originate from 
                the same AST rule, helping to detect repetition and structural symmetry 
                in the grammar.
"""]

    

system_message = [{'role': 'system', 'content': system_prompt[0]}]
old_trees = []
old_examples = []
def bubble_pair_api(trees):
    
    
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
        response = bubble_pair_api(chat_log)
        print(f"AI: {response}")
        chat_log.append({'role': 'assistant', 'content': response})
        if len(chat_log) > 10:
            chat_log.pop(0)
            chat_log.pop(0)

