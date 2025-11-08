import heapq
from collections import defaultdict

class HuffmanNode:
    def __init__(self, char=None, freq=None, left=None, right=None):
        self.char = char
        self.freq = freq
        self.left = left
        self.right = right

    def __lt__(self, other):
        return self.freq < other.freq


def build_tree(text):
    frequency = defaultdict(int)
    for char in text:
        frequency[char] += 1

    heap = [HuffmanNode(char, freq) for char, freq in frequency.items()]
    heapq.heapify(heap)

    while len(heap) > 1:
        left = heapq.heappop(heap)
        right = heapq.heappop(heap)
        merged = HuffmanNode(freq=left.freq + right.freq, left=left, right=right)
        heapq.heappush(heap, merged)

    return heap[0]


def generate_codes(node, current="", codes=None):
    if codes is None:
        codes = {}

    if node.char is not None:
        codes[node.char] = current

    if node.left:
        generate_codes(node.left, current + "0", codes)
    if node.right:
        generate_codes(node.right, current + "1", codes)

    return codes


# ✅ renamed
def encode_text(text):
    root = build_tree(text)
    codes = generate_codes(root)
    encoded_text = ''.join(codes[ch] for ch in text)
    return encoded_text, root


# ✅ renamed
def decode_text(encoded_text, root):
    decoded = ""
    node = root

    for bit in encoded_text:
        node = node.left if bit == "0" else node.right
        if node.char:
            decoded += node.char
            node = root

    return decoded
