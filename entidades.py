"""Coincidencias de personas ya identificadas dentro del mismo documento."""
import re
import unicodedata


def normalized_word(value):
    return ''.join(c for c in unicodedata.normalize('NFD', value.upper()) if c.isalpha())


def edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (a != b)))
        previous = current
    return previous[-1]


def known_entity_spans(text, entities):
    """Only match names/IDs learned from this document, never general numbers."""
    tokens = list(re.finditer(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{2,}(?:[:'][A-Za-zÁÉÍÓÚÑÜáéíóúñü]+)?", text))
    normalized = [normalized_word(token.group()) for token in tokens]
    pairs = set()
    full_names = set()
    for name in entities.get('PERSONA', ()):
        parts = [normalized_word(part) for part in name.split()]
        if len(parts) >= 2:
            full_names.add(tuple(parts))
        parts = [part for part in parts if part not in {'DE', 'DEL', 'LA', 'LAS', 'LOS', 'Y'}]
        for left, right in zip(parts, parts[1:]):
            pairs.add((left, right))
        # Names in authorizations may put the surnames before the given names.
        if len(parts) >= 4:
            pairs.add((parts[-1], parts[0]))
    candidates = []
    for start in range(len(tokens)):
        for name in full_names:
            end = start + len(name)
            if tuple(normalized[start:end]) != name:
                continue
            words = tokens[start:end]
            if all(re.fullmatch(r'[ \t]+|[ \t]*\n[ \t]*',
                                text[left.end():right.start()])
                   for left, right in zip(words, words[1:])):
                a, b = words[0].start(), words[-1].end()
                candidates.append((a, b))
    for i in range(len(tokens)-1):
        a, b = tokens[i:i+2]
        separator = text[a.end():b.start()]
        if not re.fullmatch(r'[\s.:,-]+', separator) or separator.count('\n') > 1:
            continue
        for left, right in pairs:
            x, y = normalized[i:i+2]
            # Require an exact neighbouring name; OCR tolerance is limited to
            # the other word, so isolated capitalized phrases do not qualify.
            if not ((x == left and len(left) >= 3) or (y == right and len(right) >= 3)):
                continue
            if edit_distance(x, left) <= (2 if len(left) >= 5 else 0) and edit_distance(y, right) <= (2 if len(right) >= 5 else 0):
                candidates.append((a.start(), b.end()))
                break
    matched = []
    for start, stop in sorted(candidates):
        if matched and start < matched[-1][1]:
            matched[-1] = (matched[-1][0], max(stop, matched[-1][1]))
        else:
            matched.append((start, stop))
    for start, stop in matched:
        yield start, stop, 'PERSONA', text[start:stop]
    substitutions = {'0': '0Oo', '1': '1Il', '2': '2Zz', '5': '5Ss', '8': '8B'}
    for identifier in entities.get('IDENTIFICACION', ()):
        digits = re.sub(r'\D', '', identifier)
        if len(digits) < 6:
            continue
        expression = r'(?<![\w])' + r'[ .\-]*'.join('[' + substitutions.get(digit, digit) + '?]' for digit in digits) + r'(?![\w])'
        for match in re.finditer(expression, text):
            if match.group().count('?') <= 1:
                yield match.start(), match.end(), 'IDENTIFICACION', match.group()
