import time
from collections import defaultdict
from typing import List, Tuple, Set, Dict, Optional, Union
import statistics
from bubble import Bubble
from group import group, is_balanced, pre_bubble
from oracle import ExternalOracle, ParseException
from parse_tree import ParseNode, ParseTreeList, build_grammar, START
from grammar import *
from token_expansion import expand_tokens
from union import UnionFind
from replacement_utils import get_strings_with_replacement, get_strings_with_replacement_in_rule, \
    lvl_n_derivable, nt_in_tree
import config
from next_tid import allocate_tid
# from PrettyPrint import PrettyPrintTree
from label_llm import generate_label_api, regenerate_label
from bubble_llm import bubble_api
from bubble_pair_llm import bubble_pair_api
import json
import string
import functools
"""
Bulk of the Arvada algorithm.
"""

###################### Settings for ICSE'24 Submission #########################
# MAX_SAMPLES_PER_COALESCE = 50   << number of strings to sample from the     #
#                                   grammar induced by a marge. Increase to   #
#                                   increase chance of catching unsound       #
#                                   merges, at the cost of runtime.           #
# MAX_GROUP_LEN = 10              << max number of elements in a bubble.      #
#                                   Reducing will decrease runtime of algo,   #
#                                   at cost of missing some bubblings         #
#                                                                             #
# MUST_EXPAND_IN_COALESCE = False << additional setting, requiring a merge to #
#                                   not only be valid, but also expand the    #
#                                   language accepted by the learned grammar  #
# MUST_EXPAND_IN_PARTIAL= False   << same thing but for partial merges        #
###############################################################################

MAX_SAMPLES_PER_COALESCE = 50
MIN_GROUP_LEN = 3
MAX_GROUP_LEN = 10
MUST_EXPAND_IN_COALESCE = False
MUST_EXPAND_IN_PARTIAL= False

ORIGINAL_COALESCE_TIME = 0
BUILD_TIME = 0
LAST_COALESCE_TIME = 0
EXPAND_TIME = 0
MINIMIZE_TIME = 0
HDD_TIME = 0

TIME_GENERATING_EXAMPLES = 0
TIME_GROUPING = 0
REAPPLY = 0
NR_MERGE_COUNT = 0
LLM_CALLS = 0
USE_LLM = config.USE_LLM
TREEVADA = config.TREEVADA
HDD = config.HDD

def get_times():
    from replacement_utils import TIME_GENERATING_EXAMPLES_INTERNAL
    return {'FIRST_COALESCE' : ORIGINAL_COALESCE_TIME, 'BUILD': BUILD_TIME,
            'LAST_COALESCE' : LAST_COALESCE_TIME, 'EXPAND': EXPAND_TIME, 'MINIMIZE': MINIMIZE_TIME,
            'OVERALL_EXAMPLE_GEN': TIME_GENERATING_EXAMPLES + TIME_GENERATING_EXAMPLES_INTERNAL,
            'OVERALL_GROUPING': TIME_GROUPING, 'HDD_TIME': HDD_TIME, 'REAPPLY_COUNT': REAPPLY, 'NON-RECURSIVE_MERGE_COUNT': NR_MERGE_COUNT}

def check_recall(oracle, grammar: Grammar):
    """
    Helper function to check whether grammar is consistent with oracle.
    """
    positives = grammar.sample_positives(10, 10)
    for pos in positives:
        try:
            oracle.parse(pos)
        except:
            return False
    return True

def build_start_grammar(oracle, leaves, bbl_bounds = (3,10)):
    """
    ORACLE is a CachingOracle or ExternalOracle with a .parse method, which
    returns True if the example given is in the ORACLE's language

    LEAVES is a list of positive examples, each  a list of characters.

    Returns a grammar that maximally expands LEAVES w.r.t. ORACLE.
    """
    global LAST_COALESCE_TIME
    global EXPAND_TIME
    global MINIMIZE_TIME
    global MIN_GROUP_LEN 
    global MAX_GROUP_LEN
    global HDD
    global HDD_TIME
 
    MIN_GROUP_LEN, MAX_GROUP_LEN = bbl_bounds
    print('Building the starting trees...'.ljust(50), end='\r')
    trees = build_trees(oracle, leaves)
    print('Building initial grammar...'.ljust(50), end='\r')

    grammar, hdd_grammar = None, None
    
    grammar = build_grammar(trees)
    print('Coalescing nonterminals...'.ljust(50))
    s = time.time()
    grammar, new_trees, coalesce_caused, _ = coalesce(oracle, trees, grammar)
    LAST_COALESCE_TIME += time.time() - s
    if config.AI_LABEL:
        print('Relabeling nonterminals...'.ljust(50))
        new_trees, grammar = relabel_tree_nonterminals(new_trees, grammar)
    # grammar, new_trees, partial_coalesces = coalesce_partial(oracle, new_trees, grammar)
    
    s = time.time()
    grammar = expand_tokens(oracle, grammar, new_trees)
    EXPAND_TIME += time.time() - s
    s = time.time()
    grammar = minimize(grammar)
    MINIMIZE_TIME += time.time() - s
    if HDD:
        s = time.time()
        print('Performing HDD decomposition...'.ljust(50), end='\r')
        augmented = {t.derived_string(): t for t in new_trees}
        reduced_trees = hdd_decompose(new_trees, oracle, augmented)
        print(f"HDD decomposed {len(reduced_trees)} new trees.")
        HDD_TIME = time.time() - s

        new_trees += reduced_trees
        hdd_grammar = build_grammar(new_trees)
        hdd_grammar = expand_tokens(oracle, hdd_grammar, new_trees)
        hdd_grammar = minimize(hdd_grammar)
        print(str(hdd_grammar))
        
    return grammar, hdd_grammar


def build_naive_parse_trees(leaves: List[List[ParseNode]], bracket_items: List, oracle: ExternalOracle):
    """
    Builds naive parse trees for each leaf in `leaves`, assigning each unique
    character to its own nonterminal, and uniting them all under the START
    nonterminal.
    bracket_items is a list of bracket enclosed sequence lengths.
    """
    terminals = list(dict.fromkeys([leaf.payload for leaf_lst in leaves for leaf in leaf_lst]))
    reserved = [START, 'start']
    get_class = {t: t for t in terminals if t not in reserved}
    for r in reserved:
        new_nt = r
        while new_nt in terminals:
            new_nt = new_nt + "_"
        get_class[r] = new_nt
    quotes = ["\"", "\'"]

    def braces_tree(leaves: List[ParseNode], index: int, open_list: List[int], close_list: List[int], first: ParseNode = None):
        """ 
        returns a initial parse tree based on brackets.
        input: a {b c}
        parse tree: 
             START
             /  \
            a   t1
              / /\ \
              { b c }
        """

        if first:
            children = [ParseNode(get_class[first.payload], False, [first])]
        else:
            nonlocal bracket_items
            bracket_items = []
            children = []

        while index < len(leaves):
            node = leaves[index]
            token = node.payload
            
            
            if (token == "{" or token == "[" or token == "(") and index in open_list:
                index += 1
                child, index = braces_tree(leaves, index, open_list, close_list, node)
                children.append(child)

            elif (token == "}" or token == "]" or token == ")") and index in close_list:
                children.append(ParseNode(get_class[token], False, [node]))
                bracket_items.append(len(children))
                return ParseNode(allocate_tid(), False, children), index + 1

            else:
                children.append(ParseNode(get_class[token], False, [node]))
                index += 1

        bracket_items.append(len(children))
        return ParseNode(START, False, children), bracket_items.copy()

    trees=[]
    bracket_counts=[]
    avg_bracket_lengths_all_trees=[]
    str_lengths = []
    for leaf_list in leaves:
        leaf_str = [leaf.payload for leaf in leaf_list]
        open_close = is_balanced(leaf_str, is_list=True)
        if open_close:
            new_children, brackets = braces_tree(leaf_list, 0, open_close[0], open_close[1])
        else:
            print("Flat tree")
            new_children = ParseNode(START, False, [ParseNode(get_class[leaf.payload], False, [leaf]) for leaf in leaf_list])
            brackets = [len(new_children.children)]
        new_children.update_cache_info()
        try:

            oracle.parse(new_children.derived_string())

        except:
            print("\nInvalid tree constructed!")
            exit(1)

        
        trees.append(new_children)
        bracket_counts.append(len(brackets))             #brackets = per tree bracket lengths
        avg_bracket_lengths_all_trees.append(sum(brackets)/len(brackets))
        str_lengths.append(len(leaf_list))

    avg_bracket_count = sum(bracket_counts)/len(bracket_counts)    #bracket count across all trees
    avg_bracket_lengths = sum(avg_bracket_lengths_all_trees)/len(avg_bracket_lengths_all_trees)
    avg_n = sum(str_lengths)/len(str_lengths)
    print(f"Average number of brackets: {avg_bracket_count}")
    print(f"Average lengths of brackets: {avg_bracket_lengths}")
    print(f"Average tokens: {avg_n}")


    return trees

def hdd_decompose(trees: List[ParseNode], oracle: ExternalOracle, new_trees: dict[str, ParseNode]):
    """
    Hierarchical delta debugging to break down seed inputs into smaller valid inputs.
    new_trees contain the newly added decomposed trees
    """
    # pt = PrettyPrintTree(lambda x: x.children, lambda x: x.payload)
    
    cache_str = {}
    def try_parse(node: ParseNode):
        """
        Try to parse the node and remove single start->start indirections
        """
        try:
            seed = node.derived_string()
            if seed in cache_str and not cache_str[seed]:
                return False
            oracle.parse(seed)
            cache_str[seed] = True
            # seed = seed.replace(" ", "")
            if seed not in new_trees:
                if node.payload != START:
                    node.payload = START
                while len(node.children) == 1 and (not node.children[0].is_terminal) and node.payload == node.children[0].payload:
                    node = node.children[0]
                node.cache_valid = False
                node.update_cache_info()

                # Check all derivable strings with overlapping original trees
                overlapping = [t for t in new_trees.values() if len(node.all_nts() & t.all_nts()) > 1]
                
                
                overlapping = sorted(overlapping, key=lambda x: (len(node.all_nts() & x.all_nts()), len(x.cached_string)), reverse=True)
                depth1_str = lvl_n_derivable([node]+overlapping[:20], START, 2)
                for s in depth1_str:
                    if s in cache_str:
                        if cache_str[s]:
                            continue
                        else:
                            return False
                    oracle.parse(s)
                    cache_str[s] = True
                new_trees[seed] = node.copy()
                new_trees[seed].update_cache_info()
                return True
        except:
            cache_str[seed] = False
            return False
    
    def ddmin(node: ParseNode):
        """
        minimizes a flat level into a list of smaller valid levels
        """
        reduced = []
        n = len(node.children)

        granularity = 2
        while granularity <= n:
            chunk_size = n // granularity
            
            for i in range(granularity):
                start = i * chunk_size
                end = (i + 1) * chunk_size if i != granularity - 1 else n
                trial_node = node.copy()
                trial_node.children = trial_node.children[:start] + trial_node.children[end:]
                trial_node.update_cache_info()

                if try_parse(trial_node):
                    reduced.append(trial_node)
                
            for i in range(granularity):
                start = i * chunk_size
                end = (i + 1) * chunk_size if i != granularity - 1 else n
                trial_node = node.copy()
                trial_node.children = node.children[start:end]
                trial_node.update_cache_info()
                if try_parse(trial_node):
                    reduced.append(trial_node)
            if reduced:
                return reduced
            granularity += 1
                 

        return [node.copy()]
                    
                
    
    def hdd(node: ParseNode) -> List[ParseNode]:
        if node.is_terminal:
            return [node]
        
        nodes = ddmin(node)
        for node in nodes:
            for index in range(len(node.children)):
                idx_children = hdd(node.children[index].copy())
                for child in idx_children:
                    node.children[index] = child
                    node.cache_valid = False
                    node.update_cache_info()
                    try_parse(node)

        return nodes
    
    size = len(trees)   # initial size
    for tree in trees:
        copy = tree.copy()
        copy.update_cache_info()
        _ = hdd(copy)
        
    # Pick newly added stmt
    orig = list(new_trees.items())[:size]
    decomposed = list(new_trees.items())[size:]
    decomposed =sorted(decomposed, key=lambda x: len(x[0]))
    orig_trees = [x[1] for x in orig]
    decomposed_trees = [x[1] for x in decomposed]
    """  
    test each decomposed tree with most overlapping 10 original trees
    Sample strings at depth 2 to check validity
    """

    return decomposed_trees




def apply(grouping: Bubble, trees: List[ParseNode]):
    """
    `grouping` is a Bubble, i.e. a representation of a  contiguous
    sequence of nonterminals that appears someplace in `trees`.

    `trees` is a list of parse trees

    Returns a new list of trees consisting of  bubbling up the grouping
    in `grouping` for each tree in `trees`
    """
    bubble_applied = False

    def matches(group_lst, layer):
        """
        GROUP_LST is a contiguous subarray of ParseNodes that are grouped together.
        This method requires that len(GRP_LST) > 0.

        LAYER another a list of ParseNodes.

        Returns the index at which GROUP_LST appears in LAYER, and returns -1 if
        the GROUP_LST does not appear in the LAYER. Does not mutate LAYER.
        """
        ng, nl = len(group_lst), len(layer)
        for i in range(nl):
            layer_ind = i  # Index into layer
            group_ind = 0  # Index into group
            while group_ind < ng and layer_ind < nl and layer[layer_ind].payload == group_lst[group_ind].payload:
                layer_ind += 1
                group_ind += 1
            if group_ind == ng: 
                nonlocal bubble_applied
                bubble_applied = True
                return i
        return -1

    def apply_single(tree: ParseNode):
        """
        TREE is a parse tree.

        Applies the GROUPING data structure to a single tree. Applies that
        GROUPING to LAYER as many times as possible. Does not mutate TREE.

        Returns the new layer. If no updates can be made, do nothing.
        """
        group_lst, id = grouping.bubbled_elems, grouping.new_nt
        new_tree, ng = tree.copy(), len(group_lst)

        # Do replacments in all the children first
        for index in range(len(new_tree.children)):
            # (self, payload, is_terminal, children)
            old_node = new_tree.children[index]
            new_tree.children[index] = apply_single(old_node)

        # Prevent single nonterminal from bubbling up
        ind = matches(group_lst, new_tree.children) if ng < len(new_tree.children) else -1
        while ind != -1:
            # Prevent bubbling up the same nonterminal
            if not new_tree.payload == id:
                # if ng == len(new_tree.children):
                #     pass
                parent = ParseNode(id, False, new_tree.children[ind: ind + ng])
                new_tree.children[ind: ind + ng] = [parent]
                if ng>= len(new_tree.children):
                    break
                ind = matches(group_lst, new_tree.children)
            else:
                ind = -1

        new_tree.update_cache_info()
        return new_tree

    return [apply_single(tree) for tree in trees], bubble_applied

"""
Given a list of tokens, return the bubble that corresponds to the tokens
"""
def to_bubble(best_trees: List[ParseNode], tokens: List[str]):
    
    """
    Use BFS top-down to find the bubble
    """
    node_list = [tree for tree in best_trees]
    bubble_one = None
    while len(node_list) > 0:
        node = node_list.pop(0)
        n = len(node.children)
        m = len(tokens)
        """
        n = number of nodes in the target layer, m = number of tokens in the bubble
        """
        if m <= 1:
            return None
        # Skip if the number of children is less than the bubble size, instead add all children to the queue
        if n <= m:
            node_list.extend([i for i in node.children if not i.is_terminal])
            continue
        """
            ignore spaces for bubble search in the trees
        """
        no_space = [token for token in tokens if token != ' ']
        m = len(no_space)
        for i in range(n-m+1):
            for j in range(i+m, n+1):
                sub_str = [node.children[k].payload for k in range(i, j)]
                sub_str = [token for token in sub_str if token != ' ']
                if sub_str == no_space:
                    # skip spaces before i and after j
                    siblings = [node.children[k] for k in range(i, j)]
                    start = 0
                    end = len(siblings)
                    while siblings[start].payload == ' ':
                        start += 1
                    while siblings[end-1].payload == ' ':
                        end -= 1
                    # discard single item bubble
                    if end - start > 1:
                        bubble_one = Bubble(allocate_tid(), siblings[start:end])
                        return bubble_one
                if len(sub_str) > m:
                    break
        node_list.extend([i for i in node.children if not i.is_terminal])
    return bubble_one


def remove_dup(bubble_list: List[List[str]]):
    """
    Remove duplicates in a list
    """
    seen = set()
    bubble_dedup = []
    for bubble in bubble_list:
        b_tuple = tuple(bubble)
        if b_tuple not in seen:
            bubble_dedup.append(bubble)
            seen.add(b_tuple)
    return bubble_dedup
    
"""
Get the longest flat layer in the tree
"""
def get_tree_layers(best_trees, for_llm = True):

    layers = []
    def single_layer(tree):
        if tree.is_terminal:
            return
        new_layer = [child.payload for child in tree.children if child.payload != ' ']
        # remove brackets as these represent a complete level
        if new_layer and new_layer[0] in ['(', '[', '{'] and new_layer[-1] in [')', ']', '}']: 
            layers.append(new_layer[1:-1])
        else:
            layers.append(new_layer)
        for child in tree.children:
            if not child.is_terminal:
                single_layer(child)
    
    for tree in best_trees:
        single_layer(tree)
    all_layers = sorted(layers, key=lambda x: len(x), reverse=True)

    # delete duplicates
    all_layers_dedup = remove_dup(all_layers)
    
    long = [x for x in all_layers_dedup if len(x) >= 3]
    short = [x for x in all_layers_dedup if len(x) < 10 and len(x) > 1]

    if not for_llm:
        return reversed(short)
    # return layers that sums up 500 characters
    top_layers = []
    sum_len = 0
    for layer in long:
        top_layers.append(layer)
        sum_len += len(layer)
        if sum_len > 500 and len(top_layers) >10:
            break
    return top_layers

def build_trees(oracle, leaves):
    """
    ORACLE is an oracle for the grammar we seek to find. We ask the oracle
    yes or no replacement questions in this method.

    LEAVES should be a list of lists (one list for each input example), where
    each sublist contains the tokens that built that example, as ParseNodes.

    Iteratively builds parse trees by greedily choosing a substring to "bubble"
    up that passes replacement tests at each point in the algorithm, until no
    further bubble ups can be made.

    Returns a list of finished parse trees (as ParseNode) one for each list of
    leaf nodes in `leaves`.

    Algorithm:
        1. Over all top-level substrings:
            a. bubble up the substring
            b. perform replacement if possible
        2. If a replacement was possible, repeat (1)
    """
    global ORIGINAL_COALESCE_TIME
    global BUILD_TIME
    global TIME_GROUPING


    def score(trees: List[ParseNode], new_bubble: Optional[Bubble]) \
            -> Tuple[int, List[ParseNode]]:
        """
        Tries to merge nonterminals in `trees`, and returns (1, the new trees with labels)
        merged if a merge occurs. Score is 0 otherwise.

        If `new_bubble` is not None, only checks mergings that involve
        the new bubble (against each existing nonterminal if it's a 1-bubble
        and between the two introduced nonterminals if it's a 2-bubble)

        """
        # Convert LAYERS into a grammar
        grammar = build_grammar(trees)
        # t = time.time()
        grammar, new_trees, coalesce_caused, coalesced_into = coalesce(oracle, trees, grammar, new_bubble)
        # print(f"Time for coalescing: {time.time() - t}")
        new_size = grammar.size()
        if coalesce_caused:
            return 1, new_trees, coalesced_into
        else:
            return 0, trees, {}
        

    def get_updated_bubble(bubble, coalesced_into):
        """
        After a successful node merge, update the bubble elements to reflect the new nonterminal
        """
        if not coalesced_into:
            return bubble
        
        if isinstance(bubble, Bubble):
            new_nt = None
            for elem in bubble.bubbled_elems:
                if elem.payload in coalesced_into:
                    # handle multi-hop coalescing
                    new_nt = coalesced_into[elem.payload]
                    while new_nt in coalesced_into and not new_nt == coalesced_into[new_nt]:
                        new_nt = coalesced_into[new_nt]
                    elem.payload = new_nt
            if new_nt and new_nt != coalesced_into.get(new_nt, None):
                bubble.new_nt = allocate_tid()
                bubble.bubble_str = ''.join([e.payload for e in bubble.bubbled_elems])
        else:
            for bubble_single in bubble:
                new_nt = None
                for elem in bubble_single.bubbled_elems:
                    if elem.payload in coalesced_into:
                        new_nt = coalesced_into[elem.payload]
                        while new_nt in coalesced_into and not new_nt == coalesced_into[new_nt]:
                            new_nt = coalesced_into[new_nt]
                        elem.payload = new_nt
                if new_nt and new_nt != coalesced_into.get(new_nt, None):      
                    bubble_single.new_nt = allocate_tid()
                    bubble_single.bubble_str = ''.join([e.payload for e in bubble_single.bubbled_elems])
        return bubble
    
    def update_all_bubbles(all_bubbles, coalesced_into, best_trees):
        """
        After a bubble_loop call, update all bubbles to reflect the new nonterminals
        """
        for k in list(all_bubbles.keys()):
            bubble = all_bubbles[k]
            updated_bubble = get_updated_bubble(bubble, coalesced_into)
            all_bubbles.pop(k)
            bubble_tokens = [e.payload for e in updated_bubble.bubbled_elems]
            valid_bubble = to_bubble(best_trees, bubble_tokens)
            if valid_bubble:
                all_bubbles[valid_bubble.bubble_str] = valid_bubble

    
    def bubble_loop(best_trees, count, bubble_list, no_llm = False, grp_size = -1):    # delete grp_size later
        updated, nlg = False, len(bubble_list)
        loop_coalesce_into = {}
        nt_set = set()
        print(f"Bubbles in list: {nlg}")
        for i, grouping in enumerate(bubble_list):
            
            reapply = True
            last = -1
            valid_bubble = False
            while reapply:
                
                grouping = get_updated_bubble(grouping, loop_coalesce_into)
                 
                if isinstance(grouping, Bubble):
                    new_trees, applied = apply(grouping, best_trees)
                    if not applied:
                        break
                    new_score, new_trees, coalesced_into = score(new_trees, grouping)
                    grouping_str = f"Successful grouping (single): {grouping.bubbled_elems}\n    (aka {[e.derived_string() for e in grouping.bubbled_elems]}"
                        # grouping_str += f"\n     [score of {the_score}]"
                else:
                    bubble_one = grouping[0]
                    bubble_two = grouping[1]
                    new_trees, applied_one = apply(bubble_one, best_trees)
                    new_trees, applied_two = apply(bubble_two, new_trees)
                    if not (applied_one and applied_two):
                        break
                    new_score, new_trees, coalesced_into = score(new_trees, grouping)
                    grouping_str = f"Successful grouping (double): {bubble_one.bubbled_elems}, {bubble_two.bubbled_elems}"
                    grouping_str += f"\n     (aka {[e.derived_string() for e in bubble_one.bubbled_elems]}, {[e.derived_string() for e in bubble_two.bubbled_elems]}))"
                        # grouping_str += f"\n     [score of {the_score}]"
                    ### Score
                if new_score > 0:
                    best_trees = new_trees    
                    
                    # pt = PrettyPrintTree(lambda x: x.children, lambda x: x.payload)
                    # for tree in best_trees:
                    #     print(tree.to_newick())
                    #     pt(tree)

                    if i == last:
                        global REAPPLY
                        REAPPLY += 1
                        print(f"Reapply: {REAPPLY}")
                    last = i
                    print()
                    print(('[Group len %d] Bubbling iteration %d (%d/%d)...' % (grp_size, count, i + 1, nlg)).ljust(50))
                    print(grouping_str)
                    print("coalesced into: ", coalesced_into)

                    # first, flatten multi-hop coalesced_into
                    for k in list(coalesced_into.keys()):
                        v = coalesced_into[k]
                        while v in coalesced_into and not v == coalesced_into[v]:
                            v = coalesced_into[v]
                        coalesced_into[k] = v

                    # grouping = get_updated_bubble(grouping, coalesced_into)                           
                    updated, valid_bubble = True, True

                    # need to maintain another coalesced_into for the entire bubble loop?
                    loop_coalesce_into.update(coalesced_into)
               
                else:
                    reapply = False

            # if valid_bubble:
                
            #     if no_llm:
            #         break

        
        return best_trees, updated, loop_coalesce_into

    def get_llm_bubble(best_trees, one_bubble = True, retry = 0):
        """
        Get list of bubbles from LLM
        """
        layer = get_tree_layers(best_trees)
        # don't call 2-bubbles if longest layer is less than 7, it might allow redundant merges
        if not one_bubble and (not layer or len(layer[0]) < 7):
            return []
        prompt = '\n'.join([str(i) for i in layer])
        bubble_list = bubble_api(prompt) if one_bubble else bubble_pair_api(prompt)       # llm call here
        
        try:
            bubble_list = json.loads(bubble_list)['siblings']

        except:
            print("LLM failed to generate bubbles")
            return get_llm_bubble(best_trees, one_bubble, retry + 1) if retry < 3 else []
        
        return bubble_list[:100]

    best_trees = build_naive_parse_trees(leaves, [], oracle)
    print(f"Branching factor: {branching_factor(best_trees)}")
    grammar = build_grammar(best_trees)

    # pt = PrettyPrintTree(lambda x: x.children, lambda x: x.payload)
    # for tree in best_trees:
    #     print(tree.derived_string())
    #     pt(tree)

    s = time.time()
    print("Beginning coalescing...".ljust(50))
    grammar, best_trees, _, _ = coalesce(oracle, best_trees, grammar)
    
        
    ORIGINAL_COALESCE_TIME += time.time() - s


    s = time.time()
    # Main algorithm loop. Iteratively increase the length of groups allowed from MIN_GROUP_LEN to MAX_GROUP_LEN
    # break the group_size loop if no valid merge after increasing group size by threshold
    # for group_size in range(MIN_GROUP_LEN, MAX_GROUP_LEN):

    threshold = 5 if USE_LLM else 0
    count = 0
    
    all_bubbles = {}    # need to update by coalesced_into
    while threshold > 0:

        updated = True
        while updated:
            print(f"PRE-BUBBLES")
            pre_bubble_layers = get_tree_layers(best_trees, False)
            pre_bubbles = [to_bubble(best_trees, b) for b in pre_bubble_layers]
            pre_bubbles = [b for b in pre_bubbles if b]
            best_trees, updated, coalesced_into = bubble_loop(best_trees, count, pre_bubbles)
            if updated:
                update_all_bubbles(all_bubbles, coalesced_into, best_trees)

        updated = True
        while updated:
            print(f"1-BUBBLES")
            count += 1
            bubble_list = get_llm_bubble(best_trees)
            # remove duplicates
            bubble_dedup = remove_dup(bubble_list)
            # sort by length, shorter bubbles should be applied first
            # bubble_dedup = sorted(bubble_dedup, key=lambda x: len(x), reverse=True)
            # get bubbles from string
            for b in bubble_dedup:
                cand = ''.join(b)
                # if not is_balanced(cand):
                #     continue
                # pop if already in the list
                if cand in all_bubbles:
                    all_bubbles.pop(cand)
                
                grp = to_bubble(best_trees, b)
                if grp:
                    all_bubbles[cand] = grp

            # one_bubbles = sorted(all_bubbles.values(), key=lambda x: len(x.bubbled_elems))
            # keep last added 50 bubbles
            all_bubbles = dict(list(all_bubbles.items())[-100:])
            # one_bubbles = list(reversed(all_bubbles.values()))
            # sort by length, shorter bubbles should be applied first
            one_bubbles = sorted(all_bubbles.values(), key=lambda x: len(x.bubbled_elems))
            

            best_trees, updated, coalesced_into = bubble_loop(best_trees, count, one_bubbles)
                
            if updated:
                threshold = 5
                # update all_bubbles according to coalesced_into
                update_all_bubbles(all_bubbles, coalesced_into, best_trees)
            else:
                threshold -= 1
        

        updated = True
        print("2-BUBBLES")
        
        while updated:

            two_bubbles = []
            count += 1
            bubble_list_double = get_llm_bubble(best_trees, False)

            # break if not valid 2-bubbles
            try:
                for first, second in bubble_list_double:
                    cand1 = ''.join(first)
                    cand2 = ''.join(second)
                    if cand1 == cand2:
                        continue
                    if not is_balanced(cand1) or not is_balanced(cand2):
                        continue
                    grp1 = all_bubbles.get(cand1, to_bubble(best_trees, first))
                    if grp1:
                        all_bubbles[cand1] = grp1
                    grp2 = all_bubbles.get(cand2, to_bubble(best_trees, second))
                    if grp2:
                        all_bubbles[cand2] = grp2
                    if cand1 == cand2:
                        continue
                    if grp1 and grp2:
                        two_bubbles.append((grp1, grp2))
            except:
                break

            best_trees, updated, coalesced_into = bubble_loop(best_trees, count, two_bubbles)
            if updated:
                threshold = 5
                update_all_bubbles(all_bubbles, coalesced_into, best_trees)


    if TREEVADA:
        
        updated = True
        threshold = 5
        grp_size = MIN_GROUP_LEN
        while threshold:
           
            pre_bubbles = pre_bubble(best_trees)
            best_trees, updated, coalesced_into = bubble_loop(best_trees, count, pre_bubbles, True)

            group_start = time.time()
            bubble_list = group(best_trees, grp_size)
            TIME_GROUPING += time.time() - group_start
            best_trees, updated, coalesced_into = bubble_loop(best_trees, count, bubble_list, True, grp_size)

            if updated:
                threshold = 5

            group_start = time.time()
            bubble_list_double = group(best_trees, grp_size, True)
            TIME_GROUPING += time.time() - group_start
            best_trees, updated, coalesced_into = bubble_loop(best_trees, count, bubble_list_double, True, grp_size)
            
            count+=1
            if not updated:
                grp_size += 1
                threshold -= 1
            else:
                threshold = 5
                
            # HALF_MERGE = True if threshold ==1 else False
            
    BUILD_TIME += time.time() - s
    return best_trees



def coalesce_partial(oracle, trees: List[ParseNode], grammar: Grammar,
                     coalesce_target: Bubble = None):
    """
    ASSUMES: `grammar` is the grammar induced by `trees`

    Performs partial coalesces on the grammar. That is, for pairs of nonterminals (nt1, nt2), checks whether:
       if nt1 can be replaced by nt2 everywhere, are there any occurrences of nt2 where nt1 can replace nt2.
    An "occurrence" of nt2 is a location in a rule in grammar. So even if there are two separate trees
    where nt2 occurs in the subtree:
        nt0
       /  \
     nt3  nt2

     nt2 beside nt3 as a child of nt0 is considered only "one occurrence"

    For efficiency:
     While nt1 can range over all nonterminals in the grammar, nt2 ranges only over "character" nonterminals,
     that is those whose rules only expand to a single character. Character classes are allowc

    ASSUMES: coalesce(oracle, trees, grammar, coalesce_target) has been called previously. In this case, we will never
    be in the situation where (nt1, nt2) can partially coalesce and (nt2, nt1) can partially coalesce.

    """

    def partially_coalescable(replaceable_everywhere: str, replaceable_in_some_rules: str, trees: ParseTreeList) -> Dict[
        Tuple[str, Tuple[str]], List[int]]:
        """
        `replaceable_everywhere` and `replaceable_in_some_rules` are both nonterminals

        If `replaceable_in_some_rules` can replace `replaceable_everywhere` at every
        occurrence of `replaceable_everywhere`, returns the rules (expansions) in which
        `replaceable_in_some_rules` can be replaced by `replaceable_everywhere`
        """

        global TIME_GENERATING_EXAMPLES
        language_expanded = not MUST_EXPAND_IN_PARTIAL
        # Get all the expansions where `replaceable_in_some_rules` appears
        partial_replacement_locs: List[Tuple[Tuple[str, List[str]], int]] = []
        for rule_start, rule in grammar.rules.items():
            for body in rule.bodies:
                replacement_indices = [idx for idx, val in enumerate(body) if val == replaceable_in_some_rules]
                for idx in replacement_indices:
                    partial_replacement_locs.append(((rule_start, body), idx))

        s = time.time()
        # Get the set of strings derivable from `replaceable_everywhere`
        everywhere_derivable_strings = lvl_n_derivable(trees, replaceable_everywhere, 0 )

        # Get the set of strings derivable from `replaceable_in_some_rules`
        in_some_derivable_strings = lvl_n_derivable(trees, replaceable_in_some_rules, 0)

        TIME_GENERATING_EXAMPLES += time.time() - s

        # Check whether `replaceable_everywhere` is replaceable by `replaceable_in_some_rules` everywhere.
        everywhere_by_some_candidates = []
        for tree in trees:
            everywhere_by_some_candidates.extend(
                get_strings_with_replacement(tree, replaceable_everywhere, in_some_derivable_strings))


        if len(everywhere_by_some_candidates) > MAX_SAMPLES_PER_COALESCE:
            everywhere_by_some_candidates = random.sample(everywhere_by_some_candidates, MAX_SAMPLES_PER_COALESCE)
        else:
            random.shuffle(everywhere_by_some_candidates)

        if MUST_EXPAND_IN_PARTIAL and coalesce_target is not None and trees.represented_by_derived_grammar(everywhere_by_some_candidates):
            language_expanded = False
        else:
            language_expanded = MUST_EXPAND_IN_PARTIAL
            try:
                for replaced_str in everywhere_by_some_candidates:
                    oracle.parse(replaced_str)
            except Exception as e:
                return []

        if (len(everywhere_derivable_strings) == 0): return {}

        # Now check whether there are any rules where `replaeable_in_some_rules` is replaceable by
        # `replaceable_everywhere`
        replacing_positions: Dict[Tuple[str, Tuple[str]], List[int]] = defaultdict(list)
        for replacement_loc in partial_replacement_locs:
            rule, posn = replacement_loc
            candidate_strs = []
            for tree in trees:
                candidate_strs.extend(
                    get_strings_with_replacement_in_rule(tree, rule, posn, everywhere_derivable_strings))
            if len(candidate_strs) > MAX_SAMPLES_PER_COALESCE:
                candidate_strs = random.sample(candidate_strs, MAX_SAMPLES_PER_COALESCE)
            else:
                random.shuffle(candidate_strs)

            if MUST_EXPAND_IN_PARTIAL and coalesce_target is not None and trees.represented_by_derived_grammar(candidate_strs):
                replacing_positions[(rule[0], tuple(rule[1]))].append(posn)
                continue

            try:
                candidate_index = 0
                for candidate in candidate_strs:
                    candidate_index += 1
                    oracle.parse(candidate)
                with open("first_reject_log.csv", "a", encoding="utf-8") as frlog:
                    frlog.write(f"{len(candidate_strs)}|{candidate_index}|ALL_PASSED\n")
                replacing_positions[(rule[0], tuple(rule[1]))].append(posn)
                language_expanded = True
            except ParseException as e:
                with open("first_reject_log.csv", "a", encoding="utf-8") as frlog:
                    frlog.write(f"{len(candidate_strs)}|{candidate_index}|REJECTED\n")
                continue

        if MUST_EXPAND_IN_PARTIAL and coalesce_target is not None and not language_expanded:
            return []
        return replacing_positions

    def get_updated_grammar(old_grammar, partial_replacement_locs: Dict[Tuple[str, Tuple[str]], List[int]],
                            full_replacement_nt: str, nt_to_partially_replace: str, new_nt: str):
        """
        Creates a copy of `old_grammar` so that the locations in `partial_replacement_locs` are replaced by `new_nt`, and all
        occurrences of `full_relacement_nt` are replaced by `new_nt`
        """
        # Keep track of whether nt to partially replace still occurs on some rhss
        partially_replace_on_rhs = False
        grammar = old_grammar.copy()
        alt_rule = Rule(new_nt)
        for (rule_start, body), posns in partial_replacement_locs.items():
            rule_to_update = grammar.rules[rule_start]
            body_posn = rule_to_update.bodies.index(list(body))
            for posn in posns:
                rule_to_update.bodies[body_posn][posn] = new_nt
        for rule in grammar.rules.values():
            for body in rule.bodies:
                for idx in range(len(body)):
                    if body[idx] == full_replacement_nt:
                        body[idx] = new_nt
                    elif body[idx] == nt_to_partially_replace:
                        partially_replace_on_rhs = True
        # Now fixup rules to remove any duplicate productions that may have been added during replacement.
        for rule in grammar.rules.values():
            unique_bodies = []
            for body in rule.bodies:
                if body not in unique_bodies:
                    unique_bodies.append(body)
            rule.bodies = unique_bodies
        alt_rule_bodies = grammar.rules[full_replacement_nt].bodies
        alt_rule_bodies.extend(grammar.rules[nt_to_partially_replace].bodies)
        grammar.rules.pop(full_replacement_nt)
        alt_rule.bodies = alt_rule_bodies
        grammar.add_rule(alt_rule)
        if not partially_replace_on_rhs:
            grammar.rules.pop(nt_to_partially_replace)
        return grammar

    def update_tree(new_tree: ParseNode, partial_replacement_locs: Dict[Tuple[str, Tuple[str]], List[int]],
                    full_replacement_nt: str, new_nt: str):
        """
        Updates `new_tree` s.t. the locations in `partial_replacement_locs` are replaced by `new_nt`, and all
        occurrences of `full_relacement_nt` are replaced by `new_nt`.
        """
        if new_tree.is_terminal:
            return new_tree
        my_body = tuple([child.payload for child in new_tree.children])
        for c in new_tree.children:
            update_tree(c, partial_replacement_locs, full_replacement_nt, new_nt)
        if (new_tree.payload, my_body) in partial_replacement_locs:
            posns = partial_replacement_locs[(new_tree.payload, my_body)]
            for posn in posns:
                prev_child = new_tree.children[posn]
                prev_child.payload = new_nt
        if new_tree.payload == full_replacement_nt:
            new_tree.payload = new_nt

    def get_updated_trees(trees: ParseTreeList, rules_to_replace: Dict[Tuple[str, Tuple[str]], List[int]],
                          replacer_orig: str, replacer: str):
        rest = []
        for tree in trees:
            new_tree = tree.copy()
            update_tree(new_tree, rules_to_replace, replacer_orig, replacer)
            rest.append(new_tree)
        return rest

    #################### END HELPERS ########################

    nonterminals = set(grammar.rules.keys())
    nonterminals.remove("start")
    nonterminals = list(nonterminals)

    # Ranging over the nonterminals that need to be fully replaced by the
    # other in the list (other must replace this one at every location)
    if coalesce_target is not None:
        fully_replaceable = [coalesce_target.new_nt]
    else:
        fully_replaceable = nonterminals

    # List of nonterminals that can be partially replaced (find the positions
    # at which other replaces this one)
    partially_replaceable = [nonterm for nonterm in nonterminals
                             if len(grammar.rules[nonterm].bodies) == 1 and len(grammar.rules[nonterm].bodies[0]) == 1
                             and grammar.rules[nonterm].bodies[0][0] not in nonterminals]
    # partially_replaceable = [nonterm for nonterm in nonterminals
    #                          if len(grammar.rules[nonterm].bodies) == 1
    #                          and grammar.rules[nonterm].bodies[0] not in nonterminals]

    # The main work of the function.
    replacement_happened = False
    fully_replaced = {}
    trees = ParseTreeList(trees, grammar)
    for nt_to_fully_replace in fully_replaceable:
        for nt_to_partially_replace in partially_replaceable:

            # Fixups because we created the lists fully_replaceable and partially_replaceable
            # before performing replacements. So we may have some out-dated labels.
            while nt_to_fully_replace in fully_replaced and nt_to_fully_replace != START:
                nt_to_fully_replace = fully_replaced[nt_to_fully_replace]
            while nt_to_partially_replace in fully_replaced and nt_to_partially_replace != START:
                nt_to_partially_replace = fully_replaced[nt_to_partially_replace]
            if nt_to_fully_replace == nt_to_partially_replace:
                continue

            # Delegate to helper to find of if (a) nt_to_fully_replace can be replaced by nt_to_partially_replace
            # everywhere, and if so (b) return the positions at which nt_to_partially_replace can be replaced
            # by nt_to_fully_replace
            replacement_positions = partially_coalescable(nt_to_fully_replace, nt_to_partially_replace, trees)

            if len(replacement_positions) > 0:
                #print(f"we found that {nt_to_partially_replace} could replace {nt_to_fully_replace} everywhere, "
                 #     f"and {nt_to_fully_replace} could replace {nt_to_partially_replace} at : {replacement_positions}")

                if nt_to_fully_replace == START:
                    new_nt = START
                else:
                    new_nt = allocate_tid()

                grammar = get_updated_grammar(grammar, replacement_positions, nt_to_fully_replace,
                                              nt_to_partially_replace, new_nt)
                inner_trees = get_updated_trees(trees, replacement_positions, nt_to_fully_replace, new_nt)
                trees = ParseTreeList(inner_trees, grammar)
                fully_replaced[nt_to_fully_replace] = new_nt
                replacement_happened = True

    trees = trees.inner_list
    return grammar, trees, replacement_happened

def branching_factor(trees: List[ParseNode]) -> float:
    """
    Returns the average branching factor of the tree.
    """

    def single_branching_factor(tree: ParseNode) -> float:
        if tree.is_terminal:
            return 0.0
        queue = [tree]
        internal_nodes = 0
        total_children = 0
        while queue:
            node = queue.pop(0)
            internal_nodes += 1
            total_children += len(node.children)
            queue.extend([child for child in node.children if not (child.is_terminal or (len(child.children) == 1 and child.children[0].is_terminal))])
        return total_children / internal_nodes if internal_nodes > 0 else 0.0
    
    return sum(single_branching_factor(tree) for tree in trees) / len(trees) if trees else 0.0



def check_epsilon(oracle, trees: List[ParseNode], grammar: Grammar):
    """
    Checks if any nonterminal can be removed from the grammar. If so, removes it.
    """

    def remove_nt(tree, nt):
        
        if not tree.is_terminal:
            tree.children = [c for c in tree.children if c.payload != nt]
            for c in tree.children:
                if not c.is_terminal:
                    remove_nt(c, nt)

    nonterminals = sorted(grammar.rules.items(), key=lambda x: x[1].depth)
    nonterminals = [x[0] for x in nonterminals]
    nonterminals.remove("start")
    # nonterminals.remove("epsilon")
    new_trees = []
    for nonterminal in nonterminals:
        ep_valid, strs = replacement_valid(oracle, [""], nonterminal, trees)
        if ep_valid:
            print(f"epsilon valid: {nonterminal}")
            for tree in trees:
                if nonterminal in tree.cached_nts:
                    new_tree = tree.copy()
                    remove_nt(new_tree, nonterminal)
                    new_tree.update_cache_info()
                    new_trees.append(new_tree)
            # for nt in grammar.rules:
            #     for body in grammar.rules[nt].bodies:
            #         if nonterminal in body:
            #             print(f"rules: {nt} -> {body}")
    trees += new_trees
    grammar = build_grammar(trees)
    return grammar, trees

    
def replacement_valid(oracle, replacer_derivable_strings, replacee, trees : ParseTreeList) -> Tuple[bool, List[str]]:
    """
    Returns true if every string derivable from `replacee` in `trees` can be replaced
    by every string in `replacer_derivable_strings`
    **Replacing set() as it doesn't preserve the order. We want to get rid of all non-determinism.
    """

    # Get the set of positive examples with strings derivable from replacer
    # replaced with strings derivable from replacee
    replaced_strings = {}
    for tree in [tree for tree in trees.inner_list if nt_in_tree(tree, replacee)]:
        for s in get_strings_with_replacement(tree, replacee, replacer_derivable_strings):
            replaced_strings[s] = True

    if len(replaced_strings) == 0:
        # TODO: See the failing doctest in bubble.py. Pickle below for a "real" example
        #import pickle
        #pickle.dump(coalesce_target, open('overlap-bug.pkl', "wb"))
        #print(f"Oopsie with {coalesce_target}.\nPretty sure this is an overlap bug that I know of.... so let's just skip it")
        return False, []
    #assert (replaced_strings)

    replaced_strings = list(replaced_strings.keys())
    # replaced_strings = sorted(replaced_strings)
    if len(replaced_strings) > MAX_SAMPLES_PER_COALESCE:
        replaced_strings = random.sample(replaced_strings, MAX_SAMPLES_PER_COALESCE)
        # replaced_strings = replaced_strings[:MAX_SAMPLES_PER_COALESCE]
    else:
        random.shuffle(replaced_strings)

    # Return True if all the replaced_strings are valid
    idx = 0
    for s in replaced_strings:
        idx += 1
        try:
            oracle.parse(s)
        except:
            with open("first_reject_log.csv", "a", encoding="utf-8") as frlog:
                frlog.write(f"{len(replaced_strings)}|{idx}|REJECTED\n")
            return False, []
    with open("first_reject_log.csv", "a", encoding="utf-8") as frlog:
        frlog.write(f"{len(replaced_strings)}|{idx}|ALL_PASSED\n")
    return True, replaced_strings


def get_llm_label(first, second, tree_list, existing_labels=None, max_attempts=5):
    """
    Suggests a nonterminal label using the LLM while avoiding conflicts with
    labels that are already reserved in the current relabeling pass.
    """
    global LLM_CALLS

    existing_labels = set() if existing_labels is None else set(existing_labels)
    s1 = random.choice(list(tree_list.derivable_in_trees(first))) if first else ""
    s2 = random.choice(list(tree_list.derivable_in_trees(second))) if second else s1

    if any(s == " " for s in [s1, s2]):
        class_nt = "ws"
    else:
        LLM_CALLS += 1
        class_nt = generate_label_api((s1, s2))
    print(f"LLM suggested label: {class_nt} for \"{s1}\" and \"{s2}\"")

    old_labels = {class_nt: 1}
    attempts = 0
    while class_nt in existing_labels:
        class_nt = regenerate_label((s1, s2), list(old_labels.keys()))
        LLM_CALLS += 1
        old_labels[class_nt] = 1
        attempts += 1
        if attempts >= max_attempts:
            while class_nt in old_labels or class_nt in existing_labels:
                if not class_nt[-1].isdigit():
                    class_nt += "_1"
                else:
                    i = len(class_nt) - 1
                    while i >= 0 and class_nt[i].isdigit():
                        i -= 1
                    number_part = class_nt[i + 1:]
                    class_nt = class_nt[:i + 1] + str(int(number_part) + 1)
            old_labels[class_nt] = 1
        print(f" Next suggestion: {class_nt}")

    return class_nt

def relabel_tree_nonterminals(trees: List[ParseNode], grammar: Grammar):
    """
    Relabels all nonterminal nodes in the fully constructed trees with
    LLM-suggested names, then returns the relabeled trees and rebuilt grammar.
    """
    # identifies the dummy parent non-terminals for leaves
    def is_dummy(nonterminal: str) -> bool:
        if len(grammar.rules[nonterminal].bodies) == 1 and \
            grammar.rules[nonterminal].bodies[0]  == [f"\"{nonterminal}\""]:
            return True
        return False

    def relabel_node(node: ParseNode, label_map: Dict[str, str]):
        if node.is_terminal:
            return
        node.payload = label_map.get(node.payload, node.payload)
        for child in node.children:
            relabel_node(child, label_map)

    tree_list = ParseTreeList(trees, grammar)
    nonterminals = sorted(grammar.rules.items(), key=lambda item: (item[1].depth, item[0]), reverse=True)
    label_map = {START: START}
    reserved_labels = {START, 'start'}

    for nonterminal, _ in nonterminals:
        if nonterminal in reserved_labels:
            continue
        if is_dummy(nonterminal):
            reserved_labels.add(nonterminal)
            continue
        label_map[nonterminal] = get_llm_label(nonterminal, nonterminal, tree_list, reserved_labels)
        reserved_labels.add(label_map[nonterminal])

    relabeled_trees = [tree.copy() for tree in trees]
    for tree in relabeled_trees:
        relabel_node(tree, label_map)
        tree.update_cache_info()

    return relabeled_trees, build_grammar(relabeled_trees)

def coalesce(oracle, trees: List[ParseNode], grammar: Grammar,
             coalesce_target: Bubble = None):
    """
    ORACLE is a Oracle for the grammar we seek to find. We ask the oracle
    yes or no replacement questions in this method.

    TREES is a list of fully constructed parse trees.

    GRAMMAR is a GrammarNode that is the disjunction of the TREES.

    COALESCE_TARGET is the nonterminal we should be checking coalescing against,
    else due a quadratic check of all nonterminals against each other.

    This method coalesces nonterminals that are equivalent to each other.
    Equivalence is determined by replacement.

    RETURNS: the grammar after coalescing, the parse trees after coalescing,
    and whether any nonterminals were actually coalesced with each other
    (found equivalent).
    """


    def replacement_valid_and_expanding(nt1, nt2, trees: ParseTreeList):
        """
        Returns true if nt1 and nt2 can be merged in the grammar while expanding the set of inputs accepted
        by the grammar, and not admitting any invalid inputs.
        """

        global TIME_GENERATING_EXAMPLES

        s = time.time()
        if isinstance(coalesce_target, tuple):
            nt1_derivable_strings = lvl_n_derivable(trees, nt1, 1)
            nt2_derivable_strings = lvl_n_derivable(trees, nt2, 1)
        else:
            nt1_derivable_strings = lvl_n_derivable(trees, nt1, 0)
            nt2_derivable_strings = lvl_n_derivable(trees, nt2, 0)
        TIME_GENERATING_EXAMPLES += time.time() - s

        # First check if the replacement is expanding
        if MUST_EXPAND_IN_COALESCE and coalesce_target is not None and nt1_derivable_strings == nt2_derivable_strings:
            return False

        nt1_valid, nt1_check_strings = replacement_valid(oracle, nt1_derivable_strings, nt2, trees)
        nt2_valid, nt2_check_strings = replacement_valid(oracle, nt2_derivable_strings, nt1, trees)

        if MUST_EXPAND_IN_COALESCE and coalesce_target is not None:
            if trees.represented_by_derived_grammar(nt1_check_strings) and \
                trees.represented_by_derived_grammar(nt2_check_strings):
                return False
        if nt1_valid and nt2_valid:
            return "<TRUE>"
        
        # Non-recursive merge case
        if nt2_valid and isinstance(coalesce_target, Bubble) and coalesce_target.new_nt == nt1:
            nt2_is_child = any(nt2 in bodies for bodies in grammar.rules[nt1].bodies)
            if nt2_is_child:
                return nt2

        return False

    def rewrite_recursive_targets(node: ParseNode, target: str, label: str):
        if node.is_terminal:
            return

        """
            bubble              bubble
            /   \       ->      /   \ 
          nt1   target        nt1   label

             target              target
               |        ->         |
            children             label
                                   |
                                children
        """
        if node.payload == coalesce_target.new_nt:
            # node.cache_valid = False
            for child in node.children:
                if child.payload == target:
                    child.payload = label
        elif node.payload == target:
            new_node = ParseNode(label, False, node.children)
            node.children = [new_node]

        for child in node.children:
            rewrite_recursive_targets(child, target, label)
        return node

    def get_updated_trees(get_class: Dict[str, str], trees):

        def replace_coalesced_nonterminals(node: ParseNode):
            """
                Rewrites node so that coalesced nonterminals point to their
                class nonterminal. For non-coalesced nonterminals, get_class
                just gives the original nonterminal
                """
            if node.is_terminal:
                return
            else:
                node.payload = get_class.get(node.payload, node.payload)
                for child in node.children:
                    replace_coalesced_nonterminals(child)

        def fix_double_indirection(node: ParseNode):
            """
                Fix parse trees that have an expansion of the for tx->tx (only one child)
                since we've removed such double indirection while merging nonterminals
                """
            if node.is_terminal:
                return

            while len(node.children) == 1 and node.children[0].payload == node.payload and not node.children[0].is_terminal:
                # Won't go on forever because eventually length of children will be not 1,
                # or the children's payload will not be the same as the top node (e.g. if
                # the child is a terminal)
                node.children = node.children[0].children

            for child in node.children:
                fix_double_indirection(child)

        new_trees = []
        for tree in trees:
            new_tree = tree.copy()
            replace_coalesced_nonterminals(new_tree)
            fix_double_indirection(new_tree)
            new_tree.update_cache_info()
            new_trees.append(new_tree)
        return new_trees
    # classes = {class_nt: [first, second]}
    # get_class = {first: class_nt, second: class_nt}
    def get_updated_grammar(classes: Dict[str, List[str]], get_class: Dict[str, str], grammar):
        # Traverse through the grammar, and update each nonterminal to point to
        # its class nonterminal
        new_grammar = grammar.copy()
        for nonterm in new_grammar.rules:
            # if nonterm == START:
            #     continue
            for body in new_grammar.rules[nonterm].bodies:
                for i in range(len(body)):
                    # The keys of the rules determine the set of nonterminals
                    if body[i] in get_class:
                        body[i] = get_class[body[i]]
        # Add the alternation rules for each class into the grammar
        for class_nt, nts in classes.items():
            rule = Rule(class_nt)
            max_depth = max([new_grammar.rules[nt].depth for nt in nts])
            for nt in nts:
                old_rule = new_grammar.rules.pop(nt)
                for body in old_rule.bodies:
                    # Remove infinite recursions
                    if body == [class_nt]:
                        continue
                    rule.add_body(body)
            new_grammar.add_rule(rule, max_depth)
        return new_grammar

    def update_checked(checked, coalesced_into):
        """
        Updates the checked set to reflect the new coalesced_into mapping
        """
        new_checked = checked.copy()
        for pair in checked:
            first, second = pair
            while first in coalesced_into and first != coalesced_into[first]:
                first = coalesced_into[first]
            while second in coalesced_into and second != coalesced_into[second]:
                second = coalesced_into[second]
            if first == second:
                continue
            if pair != (first, second):
                new_checked.add((first, second))
                new_checked.add((second, first))
        return new_checked
    
    # Define helpful data structures
    # store non-terminals depth-wise across all trees
    nonterminals = sorted(grammar.rules.items(), key=lambda x: x[1].depth)
    nonterminals = [x[0] for x in nonterminals]
    nonterminals.remove("start")

    # Get all unique pairs of nonterminals
    pairs = []
    if isinstance(coalesce_target, Bubble):
        first = coalesce_target.new_nt
        for second in nonterminals:
            if first == second:
                continue
            pairs.append((first, second))
    elif isinstance(coalesce_target, tuple):
        pair = (coalesce_target[0].new_nt, coalesce_target[1].new_nt)
        pairs.append(pair)
    else:
        for i in range(len(nonterminals)):
            for j in range(i + 1, len(nonterminals)):
                first, second = nonterminals[i], nonterminals[j]
                pairs.append((first, second))

    coalesce_caused = False
    coalesced_into = {}
    checked = set()
    tree_list = ParseTreeList(trees, grammar)
    merges = 0
    for pair in pairs:
        first, second = pair
        # update the pair for the new grammar, because the pair was created before
        # we performed any merges. If one of the labels was merged, replace it with
        # its new label.
        while first in coalesced_into and first != coalesced_into[first]:
            first = coalesced_into[first]
        while second in coalesced_into and second != coalesced_into[second]:
            second = coalesced_into[second]
        # and check that it's still valid
        if first == second:
            continue
        if (first, second) in checked:
            continue
        else:
            checked.add((first, second))
            checked.add((second, first))

        update_required = False
        replacement = replacement_valid_and_expanding(first, second, tree_list)
        if replacement:

            classes = {}
            get_class = {}
            # If the nonterminals can replace each other in every context, they are replaceable
            if replacement == "<TRUE>":
                if first == START or second == START:
                    class_nt = START
                else:
                    class_nt = allocate_tid()

                classes = {class_nt: [first, second]}
                get_class = {first: class_nt, second: class_nt}
                coalesced_into[first] = class_nt
                coalesced_into[second] = class_nt
                update_required = True
                merges += 1

            # nt2 is the merge target, this is for checking non-recursive merge
            elif replacement == second:
                """
                nt1
                /   \
            nt2  nt3
                nt2 replaces nt1 only, nt1 doesn't replace nt2. Rewrite such instances of nt2 to check other non-recursive intances of nt2 merge with nt1.
                """
                
                label = allocate_tid()
                tmp_trees = [rewrite_recursive_targets(tree.copy(), second, label) for tree in trees]
                [tree.update_cache_info() for tree in tmp_trees]
                tmp_grammar = build_grammar(tmp_trees)
                tmp_tree_list = ParseTreeList(tmp_trees, tmp_grammar)
                # print("Checking non-recursive merge")
                replacement = replacement_valid_and_expanding(first, second, tmp_tree_list)
                if replacement == "<TRUE>":
                    coalesce_caused = True
                    update_required = True
                    # class_nt = get_llm_label(first, second, tmp_tree_list, tmp_grammar)
                    # nr_nt = get_llm_label(label, label, tmp_tree_list, tmp_grammar)
                    if first == START or second == START:
                        class_nt = START
                    else:
                        class_nt = allocate_tid()

                    classes = {class_nt: [first, second]}
                    get_class = {first: class_nt, second: class_nt}
                    coalesced_into[first] = class_nt
                    coalesced_into[second] = class_nt

                    tree_list = tmp_tree_list
                    grammar = tmp_grammar

                    print(f"{second} replaces {first}")
                    global NR_MERGE_COUNT
                    NR_MERGE_COUNT += 1
                
            # Update the grammar and the trees
            if update_required:
                coalesce_caused = True
                grammar = get_updated_grammar(classes, get_class, grammar)
                new_inner_trees = get_updated_trees(get_class, tree_list.inner_list)
                checked = update_checked(checked, coalesced_into)
                tree_list = ParseTreeList(new_inner_trees, grammar)

    trees = tree_list.inner_list

    return grammar, trees, coalesce_caused, coalesced_into

def handle_special_nonterminals(grammar: Grammar, old_nt, new_nt):
    """
    Lark doesn't allow special characters as nonterminal labels.
    This function replaces all occurances of OLD_NT with NEW_NT
    """
    for rule in grammar.rules.values():
        for body in rule.bodies:
            for i in range(len(body)):
                if body[i] == old_nt:
                    body[i] = new_nt
    # Remove the old rule
    rule = grammar.rules.pop(old_nt)
    rule.start = new_nt
    # Add the new rule
    grammar.add_rule(rule)

def minimize(grammar):
    """
    Mutative method that deletes repeated rules from GRAMMAR and removes
    unnecessary layers of indirection..
    """

    def remove_inf_recursion(grammar: Grammar):
        """
        Removes all infinite recursions from the grammar.
        """
        for rule in grammar.rules.values():
            bodies = rule.bodies
            for body in bodies[:]:
                if body == [rule.start]:
                    bodies.remove(body)




    def remove_repeated_rules(grammar: Grammar):
        """
        Mutative method that removes all repeated rule bodies in GRAMMAR.
        """
        for rule in grammar.rules.values():
            remove_idxs = []
            bodies_so_far = set()
            for i, body in enumerate(rule.bodies):
                body_str = ''.join(body)
                if body_str in bodies_so_far:
                    remove_idxs.append(i)
                else:
                    bodies_so_far.add(body_str)
            for idx in reversed(remove_idxs):
                rule.bodies.pop(idx)

    def update(grammar: Grammar, map):
        """
        Given a MAP with nonterminals as keys and list of strings as values,
        replaces every occurance of a nonterminal in MAP with its corresponding
        list of symbols in the GRAMMAR. Then, the rules defining
        the keys nonterminals in MAP in the grammar are removed.

        The START nonterminal must not appear in MAP, because its rule cannot
        be deleted.
        """
        assert (START not in map)
        for rule in grammar.rules.values():
            for body in rule.bodies:
                to_fix = [elem in map for elem in body]
                # Reverse to ensure that we don't mess up the indices
                while any(to_fix):
                    ind = to_fix.index(True)
                    nt = body[ind]
                    body[ind:ind + 1] = map[nt]
                    to_fix = [elem in map for elem in body]
        remove_lhs = [lhs for lhs in grammar.rules.keys() if lhs in map]
        for lhs in remove_lhs:
            grammar.rules.pop(lhs)
        grammar.cached_parser_valid = False
        grammar.cached_str_valid = False
        return grammar

    # Remove all the repeated rules from the grammar
    remove_repeated_rules(grammar)

    # Finds the set of nonterminals that expand directly to a single terminal
    # Let the keys of X be the set of these nonterminals, and the corresponding
    # values be the the SymbolNodes derivable from those nonterminals
    X, updated = {}, True  # updated determines the stopping condition

    while updated:
        updated = False
        for rule_start in grammar.rules:
            rule = grammar.rules[rule_start]
            bodies = rule.bodies
            if len(bodies) == 1 and len(bodies[0]) == 1 and (bodies[0][0] not in grammar.rules or bodies[0][0] in X):
                body = bodies[0]
                if rule.start not in X and rule.start != START:
                    X[rule.start] = [X[elem][0] if elem in X else elem for elem in body]
                    updated = True

    # Update the grammar so that keys in X are replaced by values
    grammar = update(grammar, X)

    # Finds the set of nonterminals that expand to a single string and that are
    # only used once in the grammar. Let the keys of Y be the set of these
    # nonterminals, and the corresponding values be the SymbolNodes derivable
    # from those nonterminals
    counts = defaultdict(int)
    for rule_node in grammar.rules.values():
        for rule_body in rule_node.bodies:
            for symbol in rule_body:
                if symbol in grammar.rules:
                    n = symbol
                    counts[n] += 1

    # Update the grammar so that keys in X are replaced by values
    used_once = [k for k in counts if counts[k] == 1 and k != START]
    Y = {k: grammar.rules[k].bodies[0] for k in used_once if len(grammar.rules[k].bodies) == 1}
    grammar = update(grammar, Y)

    remove_repeated_rules(grammar)
    # check for special characters in the nonterminal names, underscores are allowed
    for rule in list(grammar.rules.values()):
        if any(c in rule.start for c in string.punctuation.replace('_','')) or (rule.start and rule.start[0].isdigit()):
            handle_special_nonterminals(grammar, rule.start, allocate_tid())
        if any(c.isupper() for c in rule.start):
            handle_special_nonterminals(grammar, rule.start, rule.start.lower())

    remove_inf_recursion(grammar)

    return grammar
