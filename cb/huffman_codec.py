# cb/huffman_codec.py

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


# ✅ Return encoded text + dictionary (NOT tree)
def encode_text(text):
    root = build_tree(text)
    codes = generate_codes(root)
    encoded_text = ''.join(codes[ch] for ch in text)

    return encoded_text, codes   # <= SEND THIS OVER WS (JSON SERIALIZABLE)


# ✅ Decode using reverse lookup
def decode_text(encoded_text, codes):
    reverse_codes = {v: k for k, v in codes.items()}

    decoded = ""
    buffer = ""

    for bit in encoded_text:
        buffer += bit
        if buffer in reverse_codes:
            decoded += reverse_codes[buffer]
            buffer = ""

    return decoded
