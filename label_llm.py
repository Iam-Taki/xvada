#from openai import OpenAI
import re
#client = OpenAI()

SYSTEM_PROMPT = (
    "You are an AI assistant. You will label the internal nodes in the parse tree of any arbitrary program. "
    "The root node of the parse tree has label stmt. You will be given a pair of substrings derivable from the same non-terminal symbol. "
    "Your job is to label the non-terminal symbol. Look at some example substring pairs and their labels."
)
EXAMPLES = [
    # (substring pair, label)
    ("'(n+n)','n'", "numexpr, this non-terminal can derive both the substrings (n+n) and n"),
    ("'L = (n+n)','while true do L = (n+n)'", "stmt, this non-terminal can derive both the substrings 'L = (n+n)' and 'while true do L = (n+n)'"),
    ("'(n+n) == n','true'", "boolexpr, this non-terminal can derive both the substrings '(n+n) == n' and 'true'"),
]
OUTPUT_INSTRUCTION = (
    "The output must be a single label, never any explanation. Use underscore if needed, never hyphen or any special character."
)

def clean_label(label):
    # Remove all non-alphanumeric characters except underscore
    cleaned = re.sub(r'[^a-zA-Z0-9_]', '', label)
    # Remove leading digits
    cleaned = re.sub(r'^\d+', 't_', cleaned)
    return cleaned

def build_base_messages():
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    for substr_pair, label in EXAMPLES:
        messages.append({'role': 'system', 'name': 'example_substrings', 'content': substr_pair})
        messages.append({'role': 'system', 'name': 'example_non-terminal', 'content': label})
    messages.append({'role': 'system', 'content': OUTPUT_INSTRUCTION})
    return messages
history = []
def generate_label_api(str_pair):
    global history
    messages = build_base_messages() + [{'role': 'system', 'name': 'history', 'content': f"Previous substring pairs and their labels: {history}"}]
    messages.append({'role': 'user', 'content': f"'{str_pair[0]}'\n'{str_pair[1]}'"})
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        seed=12345,
        max_tokens=5,
        temperature=0
    )
    ai_response = response.choices[0].message.content.strip()
    ai_response = ai_response.split()[0] if ai_response else "t_default"
    ai_response = clean_label(ai_response.replace('whitespace', ' ').lower())
    history.append((str_pair, ai_response))
    history = history[-10:]  # Keep only the last 10 entries to avoid overflow
    return ai_response
def regenerate_label(str_pair, old_labels):
    messages = build_base_messages()
    feedback = (
        f"These labels '{old_labels}' are already in use. Suggest a different label "
        "for the following substrings. You should: "
        "- Use variations from the substrings to generate the label. "
        "- Don't suggest a label that is already in use."
    )
    messages.append({'role': 'system', 'name': 'feedback', 'content': feedback})
    messages.append({'role': 'user', 'content': f"'{str_pair[0]}'\n'{str_pair[1]}'"})
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        seed=12345,
        max_tokens=5,
        temperature=0
    )
    ai_response = response.choices[0].message.content.strip()
    ai_response = ai_response.split()[0] if ai_response else "t_default"
    ai_response = clean_label(ai_response.replace('whitespace', ' ').lower())
    return ai_response

if __name__ == '__main__':
    print("Welcome to the interactive GPT chat. Type 'quit' to exit.")
    chat_log = []
    while True:
        user_prompt = input("You: ")
        if user_prompt.lower() == 'quit':
            break
        # For interactive mode, use the base messages plus the chat log
        messages = build_base_messages() + chat_log + [{'role': 'user', 'content': user_prompt}]
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            seed=12345,
            max_tokens=50,
            temperature=0
        )
        ai_response = response.choices[0].message.content.strip()
        print(f"AI: {ai_response}")
        chat_log.append({'role': 'user', 'content': user_prompt})
        chat_log.append({'role': 'assistant', 'content': ai_response})
        # Limit chat log size to avoid context overflow
        if len(chat_log) > 10:
            chat_log = chat_log[-10:]